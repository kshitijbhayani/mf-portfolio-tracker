"""Live NAV data client.

Uses the free, no-key `mfapi.in <https://www.mfapi.in/>`_ service, which mirrors
the official AMFI India NAV feed as JSON. Endpoints used:

* ``GET /mf``            -> list of every scheme: ``[{schemeCode, schemeName}]``
* ``GET /mf/{code}``     -> scheme metadata + full NAV history (latest first)

The full scheme list (~40k entries, a few MB) is cached to disk for a day so
fund search stays instant and offline-friendly.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .db import APP_DIR, NavQuote

BASE_URL = "https://api.mfapi.in"
SCHEME_LIST_CACHE = os.path.join(APP_DIR, "scheme_list.json")
SCHEME_LIST_TTL = 24 * 3600  # seconds
_TIMEOUT = 20
_UA = "MFPortfolioTracker/1.0 (+desktop)"


@dataclass
class Scheme:
    code: str
    name: str


class ApiError(Exception):
    pass


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network, json, http -> single surface
        raise ApiError(f"Could not reach NAV service: {exc}") from exc


# ----- scheme master list ----------------------------------------------
def load_scheme_list(force: bool = False) -> list[Scheme]:
    os.makedirs(APP_DIR, exist_ok=True)
    fresh = (
        not force
        and os.path.exists(SCHEME_LIST_CACHE)
        and (time.time() - os.path.getmtime(SCHEME_LIST_CACHE)) < SCHEME_LIST_TTL
    )
    if fresh:
        try:
            with open(SCHEME_LIST_CACHE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return [Scheme(str(s["schemeCode"]), s["schemeName"]) for s in raw]
        except Exception:
            pass  # fall through to refetch

    raw = _get(f"{BASE_URL}/mf")
    if not isinstance(raw, list):
        raise ApiError("Unexpected scheme list response")
    try:
        with open(SCHEME_LIST_CACHE, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
    except Exception:
        pass
    return [Scheme(str(s["schemeCode"]), s["schemeName"]) for s in raw]


def search_schemes(query: str, limit: int = 50) -> list[Scheme]:
    query = query.strip().lower()
    if not query:
        return []
    terms = query.split()
    out: list[Scheme] = []
    for s in load_scheme_list():
        name = s.name.lower()
        if all(t in name for t in terms):
            out.append(s)
            if len(out) >= limit:
                break
    return out


# ----- live NAV --------------------------------------------------------
def _parse_nav(entry: dict) -> Optional[tuple[float, str]]:
    try:
        nav = float(entry["nav"])
    except (KeyError, ValueError, TypeError):
        return None
    # mfapi dates are dd-mm-yyyy; normalise to ISO for storage/sorting.
    d = entry.get("date", "")
    try:
        iso = datetime.strptime(d, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        iso = d
    return nav, iso


def fetch_nav(scheme_code: str) -> NavQuote:
    raw = _get(f"{BASE_URL}/mf/{scheme_code}")
    if not isinstance(raw, dict):
        raise ApiError("Unexpected NAV response")
    data = raw.get("data") or []
    meta = raw.get("meta") or {}
    name = meta.get("scheme_name", "")
    if not data:
        raise ApiError(f"No NAV history for scheme {scheme_code}")

    latest = _parse_nav(data[0])
    if latest is None:
        raise ApiError(f"Bad NAV value for scheme {scheme_code}")
    nav, nav_date = latest

    prev_nav: Optional[float] = None
    if len(data) > 1:
        p = _parse_nav(data[1])
        if p is not None:
            prev_nav = p[0]

    return NavQuote(
        scheme_code=str(scheme_code),
        scheme_name=name,
        nav=nav,
        nav_date=nav_date,
        prev_nav=prev_nav,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def scheme_meta(scheme_code: str) -> dict:
    """Return the ``meta`` block (category, fund house, type) for a scheme."""
    raw = _get(f"{BASE_URL}/mf/{scheme_code}")
    if isinstance(raw, dict):
        return raw.get("meta") or {}
    return {}
