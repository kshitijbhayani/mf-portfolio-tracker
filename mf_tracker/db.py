"""SQLite persistence layer for the MF Portfolio Tracker.

The source of truth is the ``transactions`` table (one row per buy/sell). All
holdings, invested amounts and returns are *derived* from these transactions so
the books always reconcile. NAV values are cached separately so the app stays
usable offline and to avoid hammering the data feed.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

# Store the database next to the user's data, not inside the package, so it
# survives reinstalls/upgrades.
APP_DIR = os.path.join(os.path.expanduser("~"), ".mf_tracker")
DB_PATH = os.path.join(APP_DIR, "portfolio.db")


@dataclass
class Transaction:
    id: int
    scheme_code: str
    scheme_name: str
    txn_type: str  # "BUY" or "SELL"
    txn_date: str  # ISO yyyy-mm-dd
    units: float
    nav: float
    amount: float
    notes: str = ""


@dataclass
class NavQuote:
    scheme_code: str
    scheme_name: str
    nav: float
    nav_date: str
    prev_nav: Optional[float]  # previous available NAV, for day change
    fetched_at: str


class Database:
    def __init__(self, path: Optional[str] = None):
        # Resolve at call time (not import time) so tests can redirect DB_PATH.
        path = path or DB_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme_code  TEXT    NOT NULL,
                scheme_name  TEXT    NOT NULL,
                txn_type     TEXT    NOT NULL CHECK (txn_type IN ('BUY','SELL')),
                txn_date     TEXT    NOT NULL,
                units        REAL    NOT NULL,
                nav          REAL    NOT NULL,
                amount       REAL    NOT NULL,
                notes        TEXT    DEFAULT ''
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nav_cache (
                scheme_code  TEXT PRIMARY KEY,
                scheme_name  TEXT,
                nav          REAL,
                nav_date     TEXT,
                prev_nav     REAL,
                fetched_at   TEXT
            )
            """
        )
        self.conn.commit()

    # ----- transactions -------------------------------------------------
    def add_transaction(
        self,
        scheme_code: str,
        scheme_name: str,
        txn_type: str,
        txn_date: str,
        units: float,
        nav: float,
        amount: float,
        notes: str = "",
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO transactions
               (scheme_code, scheme_name, txn_type, txn_date, units, nav, amount, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (scheme_code, scheme_name, txn_type, txn_date, units, nav, amount, notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_transaction(self, txn: Transaction) -> None:
        self.conn.execute(
            """UPDATE transactions SET
                 scheme_code=?, scheme_name=?, txn_type=?, txn_date=?,
                 units=?, nav=?, amount=?, notes=?
               WHERE id=?""",
            (
                txn.scheme_code, txn.scheme_name, txn.txn_type, txn.txn_date,
                txn.units, txn.nav, txn.amount, txn.notes, txn.id,
            ),
        )
        self.conn.commit()

    def delete_transaction(self, txn_id: int) -> None:
        self.conn.execute("DELETE FROM transactions WHERE id=?", (txn_id,))
        self.conn.commit()

    def get_transactions(self, scheme_code: Optional[str] = None) -> list[Transaction]:
        if scheme_code:
            rows = self.conn.execute(
                "SELECT * FROM transactions WHERE scheme_code=? ORDER BY txn_date, id",
                (scheme_code,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM transactions ORDER BY txn_date, id"
            ).fetchall()
        return [_row_to_txn(r) for r in rows]

    def _fingerprints(self) -> set[tuple]:
        """Identity keys for existing transactions, used to skip re-imports."""
        rows = self.conn.execute(
            "SELECT scheme_code, txn_date, txn_type, units, amount FROM transactions"
        ).fetchall()
        return {
            (r["scheme_code"], r["txn_date"], r["txn_type"],
             round(r["units"], 3), round(r["amount"], 2))
            for r in rows
        }

    def import_transactions(self, txns: Iterable, skip_duplicates: bool = True) -> tuple[int, int]:
        """Bulk-insert transactions (objects with the Transaction fields).

        Returns ``(added, skipped)``. Duplicates are detected by
        (scheme_code, date, type, units, amount).
        """
        existing = self._fingerprints() if skip_duplicates else set()
        added = skipped = 0
        cur = self.conn.cursor()
        for t in txns:
            fp = (t.scheme_code, t.txn_date, t.txn_type,
                  round(t.units, 3), round(t.amount, 2))
            if skip_duplicates and fp in existing:
                skipped += 1
                continue
            cur.execute(
                """INSERT INTO transactions
                   (scheme_code, scheme_name, txn_type, txn_date, units, nav, amount, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (t.scheme_code, t.scheme_name, t.txn_type, t.txn_date,
                 t.units, t.nav, t.amount, getattr(t, "notes", "")),
            )
            existing.add(fp)
            added += 1
        self.conn.commit()
        return added, skipped

    def held_scheme_codes(self) -> list[tuple[str, str]]:
        """Return (scheme_code, scheme_name) pairs that appear in transactions."""
        rows = self.conn.execute(
            "SELECT scheme_code, scheme_name, MAX(id) FROM transactions "
            "GROUP BY scheme_code ORDER BY scheme_name"
        ).fetchall()
        return [(r["scheme_code"], r["scheme_name"]) for r in rows]

    # ----- nav cache ----------------------------------------------------
    def upsert_nav(self, q: NavQuote) -> None:
        self.conn.execute(
            """INSERT INTO nav_cache
                 (scheme_code, scheme_name, nav, nav_date, prev_nav, fetched_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(scheme_code) DO UPDATE SET
                 scheme_name=excluded.scheme_name,
                 nav=excluded.nav,
                 nav_date=excluded.nav_date,
                 prev_nav=excluded.prev_nav,
                 fetched_at=excluded.fetched_at""",
            (q.scheme_code, q.scheme_name, q.nav, q.nav_date, q.prev_nav, q.fetched_at),
        )
        self.conn.commit()

    def get_navs(self) -> dict[str, NavQuote]:
        rows = self.conn.execute("SELECT * FROM nav_cache").fetchall()
        out: dict[str, NavQuote] = {}
        for r in rows:
            out[r["scheme_code"]] = NavQuote(
                scheme_code=r["scheme_code"],
                scheme_name=r["scheme_name"],
                nav=r["nav"],
                nav_date=r["nav_date"],
                prev_nav=r["prev_nav"],
                fetched_at=r["fetched_at"],
            )
        return out

    def close(self) -> None:
        self.conn.close()


def _row_to_txn(r: sqlite3.Row) -> Transaction:
    return Transaction(
        id=r["id"],
        scheme_code=r["scheme_code"],
        scheme_name=r["scheme_name"],
        txn_type=r["txn_type"],
        txn_date=r["txn_date"],
        units=r["units"],
        nav=r["nav"],
        amount=r["amount"],
        notes=r["notes"] or "",
    )
