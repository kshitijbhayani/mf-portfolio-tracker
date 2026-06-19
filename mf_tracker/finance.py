"""Portfolio analytics: per-fund holdings, returns, day change and XIRR.

All figures are derived from the transaction ledger plus the cached NAVs, so the
UI layer stays a thin renderer over this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .db import NavQuote, Transaction


# ----- XIRR ------------------------------------------------------------
def _years_between(d0: date, d1: date) -> float:
    return (d1 - d0).days / 365.0


def xirr(cashflows: list[tuple[date, float]], guess: float = 0.1) -> Optional[float]:
    """Annualised money-weighted return for dated cashflows.

    Convention: invested money is negative, redemptions / current value positive.
    Returns a decimal rate (0.12 == 12%) or ``None`` if it cannot converge
    (e.g. all flows same sign, or too few flows).
    """
    if len(cashflows) < 2:
        return None
    flows = sorted(cashflows, key=lambda c: c[0])
    t0 = flows[0][0]
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None

    times = [_years_between(t0, d) for d, _ in flows]
    amounts = [a for _, a in flows]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for a, t in zip(amounts, times))

    def dnpv(rate: float) -> float:
        return sum(-t * a / (1.0 + rate) ** (t + 1) for a, t in zip(amounts, times))

    # Newton-Raphson with a bisection fallback for robustness.
    rate = guess
    for _ in range(100):
        try:
            f = npv(rate)
            df = dnpv(rate)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(f) < 1e-7:
            return rate
        if df == 0:
            break
        new_rate = rate - f / df
        if new_rate <= -0.9999999:
            new_rate = (rate - 0.9999999) / 2
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate

    lo, hi = -0.9999, 100.0
    try:
        flo, fhi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-7:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


# ----- holdings --------------------------------------------------------
@dataclass
class Holding:
    scheme_code: str
    scheme_name: str
    units: float = 0.0
    invested: float = 0.0          # net cost basis of units still held
    realised_pl: float = 0.0       # booked profit/loss from sells
    nav: Optional[float] = None
    nav_date: Optional[str] = None
    prev_nav: Optional[float] = None
    txns: list[Transaction] = field(default_factory=list)

    @property
    def avg_cost(self) -> Optional[float]:
        if self.units > 1e-9:
            return self.invested / self.units
        return None

    @property
    def current_value(self) -> Optional[float]:
        if self.nav is not None:
            return self.units * self.nav
        return None

    @property
    def unrealised_pl(self) -> Optional[float]:
        cv = self.current_value
        if cv is None:
            return None
        return cv - self.invested

    @property
    def return_pct(self) -> Optional[float]:
        pl = self.unrealised_pl
        if pl is None or self.invested <= 1e-9:
            return None
        return pl / self.invested * 100.0

    @property
    def day_change(self) -> Optional[float]:
        if self.nav is None or self.prev_nav is None:
            return None
        return self.units * (self.nav - self.prev_nav)

    @property
    def day_change_pct(self) -> Optional[float]:
        if self.nav is None or self.prev_nav is None or self.prev_nav == 0:
            return None
        return (self.nav - self.prev_nav) / self.prev_nav * 100.0

    @property
    def xirr_pct(self) -> Optional[float]:
        flows: list[tuple[date, float]] = []
        for t in self.txns:
            try:
                d = datetime.strptime(t.txn_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            sign = -1.0 if t.txn_type == "BUY" else 1.0
            flows.append((d, sign * t.amount))
        cv = self.current_value
        if cv is not None and self.units > 1e-9:
            flows.append((date.today(), cv))
        r = xirr(flows)
        return r * 100.0 if r is not None else None


def build_holdings(
    transactions: list[Transaction], navs: dict[str, NavQuote]
) -> list[Holding]:
    """Aggregate the ledger into per-scheme holdings using average cost basis."""
    by_code: dict[str, Holding] = {}
    for t in sorted(transactions, key=lambda x: (x.txn_date, x.id)):
        h = by_code.get(t.scheme_code)
        if h is None:
            h = Holding(t.scheme_code, t.scheme_name)
            by_code[t.scheme_code] = h
        h.scheme_name = t.scheme_name  # keep latest label
        h.txns.append(t)
        if t.txn_type == "BUY":
            h.units += t.units
            h.invested += t.amount
        else:  # SELL -> reduce units at average cost, book P&L
            avg = h.avg_cost or 0.0
            cost_removed = avg * t.units
            h.realised_pl += t.amount - cost_removed
            h.units -= t.units
            h.invested -= cost_removed
            if h.units < 1e-9:
                h.units = 0.0
                h.invested = 0.0

    for h in by_code.values():
        q = navs.get(h.scheme_code)
        if q is not None:
            h.nav = q.nav
            h.nav_date = q.nav_date
            h.prev_nav = q.prev_nav

    return sorted(by_code.values(), key=lambda x: x.scheme_name.lower())


@dataclass
class PortfolioSummary:
    invested: float = 0.0
    current_value: float = 0.0
    unrealised_pl: float = 0.0
    realised_pl: float = 0.0
    day_change: float = 0.0
    return_pct: Optional[float] = None
    day_change_pct: Optional[float] = None
    xirr_pct: Optional[float] = None
    priced_value: float = 0.0      # value of holdings we actually have a NAV for
    has_unpriced: bool = False


def summarise(holdings: list[Holding]) -> PortfolioSummary:
    s = PortfolioSummary()
    flows: list[tuple[date, float]] = []
    prev_value_total = 0.0
    for h in holdings:
        s.invested += h.invested
        s.realised_pl += h.realised_pl
        cv = h.current_value
        if cv is not None:
            s.current_value += cv
            s.priced_value += cv
            if h.prev_nav is not None:
                prev_value_total += h.units * h.prev_nav
        elif h.units > 1e-9:
            s.has_unpriced = True
        dc = h.day_change
        if dc is not None:
            s.day_change += dc
        for t in h.txns:
            try:
                d = datetime.strptime(t.txn_date, "%Y-%m-%d").date()
            except ValueError:
                continue
            sign = -1.0 if t.txn_type == "BUY" else 1.0
            flows.append((d, sign * t.amount))

    s.unrealised_pl = s.current_value - s.invested if s.invested else 0.0
    if s.invested > 1e-9:
        s.return_pct = s.unrealised_pl / s.invested * 100.0
    if prev_value_total > 1e-9:
        s.day_change_pct = s.day_change / prev_value_total * 100.0

    if s.priced_value > 1e-9:
        flows.append((date.today(), s.priced_value))
    r = xirr(flows)
    if r is not None:
        s.xirr_pct = r * 100.0
    return s
