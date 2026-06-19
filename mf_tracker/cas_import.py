"""Import an existing portfolio from a CAMS / KFintech Consolidated Account
Statement (CAS) PDF.

The heavy lifting (decrypting the password-protected PDF and parsing the
registrar layout) is done by the optional `casparser` library plus a PDF
backend (PyMuPDF). Both are imported lazily so the rest of the app keeps working
even when they are not installed — :func:`is_available` reports readiness and
:func:`missing_packages` tells the UI exactly what to ``pip install``.

Every CAS transaction is normalised into the app's own BUY/SELL model using the
**sign of the units** field: positive units (purchase, SIP, switch-in, dividend
reinvestment, gift-in) become a BUY; negative units (redemption, switch-out,
gift-out) become a SELL. Pure cash / tax rows (dividend payout, STT, stamp duty,
TDS) carry no units and are skipped.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from . import api


@dataclass
class ParsedTxn:
    scheme_code: str
    scheme_name: str
    txn_type: str          # BUY / SELL
    txn_date: str          # YYYY-MM-DD
    units: float
    nav: float
    amount: float
    notes: str = ""
    matched: bool = True   # False if we could not resolve an AMFI code


@dataclass
class CasResult:
    transactions: list[ParsedTxn] = field(default_factory=list)
    cas_type: str = ""                 # DETAILED / SUMMARY
    file_type: str = ""                # CAMS / KFINTECH
    period_from: str = ""
    period_to: str = ""
    folio_count: int = 0
    scheme_count: int = 0
    amcs: list[str] = field(default_factory=list)
    unmatched_schemes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_summary(self) -> bool:
        return self.cas_type.upper() == "SUMMARY"

    @property
    def is_depository(self) -> bool:
        """NSDL/CDSL depository CAS — holdings only, no transaction history."""
        return self.file_type.upper() in ("NSDL", "CDSL")


class CasImportError(Exception):
    pass


# ----- dependency checks ----------------------------------------------
_REQUIRED = {
    "casparser": "casparser",   # import name -> pip name
    "fitz": "pymupdf",          # PDF backend
}


def missing_packages() -> list[str]:
    """Return the pip package names that still need installing (empty == ready)."""
    missing = []
    for import_name, pip_name in _REQUIRED.items():
        if importlib.util.find_spec(import_name) is None:
            missing.append(pip_name)
    return missing


def is_available() -> bool:
    return not missing_packages()


# ----- helpers ---------------------------------------------------------
def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_date(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s[:10]


def _clean_type(raw) -> str:
    """Turn a TransactionType enum/value into a short human label."""
    name = getattr(raw, "name", None) or str(raw)
    return name.replace("_", " ").title()


# Build a name -> scheme_code index once per import for fallback matching.
def _name_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    try:
        for s in api.load_scheme_list():
            idx.setdefault(_norm(s.name), s.code)
    except api.ApiError:
        pass
    return idx


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _resolve_code(scheme: dict, name_idx: dict[str, str]) -> Optional[str]:
    amfi = scheme.get("amfi")
    if amfi:
        return str(amfi).strip()
    # Fall back to a normalised-name lookup against the AMFI master list.
    norm = _norm(scheme.get("scheme", ""))
    if norm in name_idx:
        return name_idx[norm]
    # Loose containment match as a last resort.
    for key, code in name_idx.items():
        if norm and (norm in key or key in norm):
            return code
    return None


# ----- main parse ------------------------------------------------------
def parse_cas(pdf_path: str, password: str) -> CasResult:
    """Parse a CAS PDF into normalised BUY/SELL transactions.

    Raises :class:`CasImportError` for missing dependencies, a wrong password,
    or an unreadable file.
    """
    missing = missing_packages()
    if missing:
        raise CasImportError(
            "Import needs these packages: " + ", ".join(missing)
            + ".\nInstall with:  pip install " + " ".join(missing)
        )

    import casparser  # lazy

    try:
        raw = casparser.read_cas_pdf(pdf_path, password)
    except Exception as exc:  # wrong password, corrupt file, parse failure
        msg = str(exc).lower()
        if "password" in msg or "decrypt" in msg or "incorrect" in msg:
            raise CasImportError(
                "Could not open the PDF — the password looks incorrect.\n"
                "For a CAS the password is usually your PAN (uppercase) or the "
                "password you set when requesting the statement."
            ) from exc
        raise CasImportError(f"Could not parse the CAS PDF: {exc}") from exc

    # casparser returns either a dict or a pydantic model depending on the
    # statement family — normalise to a plain dict.
    if hasattr(raw, "model_dump"):
        data = raw.model_dump()
    elif isinstance(raw, dict):
        data = raw
    else:
        raise CasImportError("Unrecognised CAS format returned by the parser.")

    result = CasResult(
        cas_type=str(data.get("cas_type", "")),
        file_type=str(data.get("file_type", "")),
        warnings=list(data.get("parse_warnings", []) or []),
    )
    period = data.get("statement_period") or {}
    result.period_from = str(period.get("from_") or period.get("from") or "")
    result.period_to = str(period.get("to") or "")

    name_idx = _name_index()

    # Two layouts:
    #   CAMS / KFintech  -> data["folios"][].schemes[].transactions[]
    #   NSDL / CDSL      -> data["accounts"][].mutual_funds[]  (holdings only)
    if data.get("accounts") is not None:
        _parse_account_based(data, result, name_idx)
    else:
        _parse_folio_based(data, result, name_idx)

    # Stable chronological order.
    result.transactions.sort(key=lambda t: (t.scheme_name.lower(), t.txn_date))
    return result


def _parse_folio_based(data: dict, result: CasResult, name_idx: dict) -> None:
    """CAMS / KFintech CAS: folios -> schemes -> transactions."""
    folios = data.get("folios") or []
    result.folio_count = len(folios)
    amcs: set[str] = set()
    scheme_count = 0

    for folio in folios:
        if folio.get("amc"):
            amcs.add(folio["amc"])
        for scheme in folio.get("schemes") or []:
            scheme_count += 1
            sname = scheme.get("scheme", "Unknown scheme")
            code = _resolve_code(scheme, name_idx)
            matched = code is not None
            if not matched:
                result.unmatched_schemes.append(sname)
                code = "NOAMFI:" + _norm(sname)[:40]

            txns = scheme.get("transactions") or []
            if txns:
                for tx in txns:
                    parsed = _convert_txn(tx, str(code), sname, matched)
                    if parsed is not None:
                        result.transactions.append(parsed)
            else:
                synth = _synthesize_from_holding(scheme, str(code), sname, matched,
                                                 result.period_to)
                if synth is not None:
                    result.transactions.append(synth)

    result.scheme_count = scheme_count
    result.amcs = sorted(amcs)


def _parse_account_based(data: dict, result: CasResult, name_idx: dict) -> None:
    """NSDL / CDSL depository CAS: accounts -> mutual_funds (holdings, no txns).

    These statements list closing units, NAV, value and (usually) total cost per
    fund but no transaction history, so each holding becomes one opening BUY.
    """
    result.cas_type = result.cas_type or "SUMMARY"
    folios: set[str] = set()
    scheme_count = 0

    for acc in data.get("accounts") or []:
        for mf in acc.get("mutual_funds") or []:
            scheme_count += 1
            if mf.get("folio"):
                folios.add(str(mf["folio"]))
            synth = _synthesize_from_nsdl_mf(mf, result, name_idx)
            if synth is not None:
                result.transactions.append(synth)

    result.scheme_count = scheme_count
    result.folio_count = len(folios)


def _synthesize_from_nsdl_mf(mf: dict, result: CasResult, name_idx: dict) -> Optional[ParsedTxn]:
    units = _f(mf.get("balance"))
    if abs(units) < 1e-9:
        return None
    sname = _clean_scheme_name(mf.get("name") or "Unknown scheme")

    code = mf.get("amfi")
    matched = bool(code)
    if matched:
        code = str(code).strip()
    else:
        norm = _norm(sname)
        code = name_idx.get(norm)
        matched = code is not None
        if not matched:
            result.unmatched_schemes.append(sname)
            code = "NOAMFI:" + norm[:40]

    nav = _f(mf.get("nav"))
    cost = _f(mf.get("total_cost"))
    if cost <= 0:
        avg = _f(mf.get("avg_cost"))
        cost = avg * units if avg > 0 else _f(mf.get("value")) or nav * units
    eff_nav = cost / units if units else nav
    return ParsedTxn(
        scheme_code=str(code),
        scheme_name=sname,
        txn_type="BUY",
        txn_date=_iso_date(result.period_to),
        units=round(units, 4),
        nav=round(eff_nav, 4),
        amount=round(cost, 2),
        notes="Holding (NSDL/CDSL CAS)",
        matched=matched,
    )


def _clean_scheme_name(name: str) -> str:
    """Drop a leading RTA product-code prefix like 'S3GD - ' from NSDL names."""
    name = name.strip()
    if " - " in name:
        head, _, rest = name.partition(" - ")
        if rest and len(head) <= 6 and head.replace(" ", "").isalnum():
            return rest.strip()
    return name


def _convert_txn(tx: dict, code: str, sname: str, matched: bool) -> Optional[ParsedTxn]:
    units = _f(tx.get("units"))
    if abs(units) < 1e-9:
        return None  # tax / stamp duty / dividend payout — no unit movement

    amount = abs(_f(tx.get("amount")))
    nav = _f(tx.get("nav"))
    if nav <= 0 and amount > 0:
        nav = amount / abs(units)
    if amount <= 0 and nav > 0:
        amount = nav * abs(units)

    txn_type = "BUY" if units > 0 else "SELL"
    return ParsedTxn(
        scheme_code=code,
        scheme_name=sname,
        txn_type=txn_type,
        txn_date=_iso_date(tx.get("date")),
        units=round(abs(units), 4),
        nav=round(nav, 4),
        amount=round(amount, 2),
        notes=_clean_type(tx.get("type")),
        matched=matched,
    )


def _synthesize_from_holding(
    scheme: dict, code: str, sname: str, matched: bool, period_to: str
) -> Optional[ParsedTxn]:
    """Summary CAS has no transaction history — build one opening BUY from the
    closing balance and cost so the holding still values correctly."""
    close = _f(scheme.get("close"))
    if abs(close) < 1e-9:
        return None
    val = scheme.get("valuation") or {}
    cost = _f(val.get("cost"))
    nav = _f(val.get("nav"))
    if cost <= 0:
        # No cost basis in summary statement — fall back to current value
        # (P&L will read ~0 until real transactions are added).
        cost = nav * close
    amount = cost
    eff_nav = cost / close if close else nav
    return ParsedTxn(
        scheme_code=code,
        scheme_name=sname,
        txn_type="BUY",
        txn_date=_iso_date(val.get("date") or period_to),
        units=round(close, 4),
        nav=round(eff_nav, 4),
        amount=round(amount, 2),
        notes="Opening balance (summary CAS)",
        matched=matched,
    )
