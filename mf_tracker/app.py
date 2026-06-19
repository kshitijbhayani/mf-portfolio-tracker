"""Tkinter desktop UI for the MF Portfolio Tracker."""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox, ttk
from typing import Optional

from . import api, cas_import
from .db import Database, Transaction


def resource_path(name: str) -> str:
    """Resolve a bundled resource for both source runs and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    # Source layout: repo root is one level above this package.
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
from .finance import Holding, build_holdings, summarise

# ----- theme -----------------------------------------------------------
BG = "#0f1724"
CARD = "#1b2536"
FG = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#3b82f6"
GREEN = "#22c55e"
RED = "#ef4444"
GRID = "#2a3650"
PIE_COLORS = [
    "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4",
    "#ec4899", "#84cc16", "#f97316", "#14b8a6", "#6366f1", "#eab308",
]


# ----- helpers ---------------------------------------------------------
def fmt_inr(value: Optional[float], decimals: int = 2, sign: bool = False) -> str:
    """Format a number in the Indian numbering system, e.g. 12,34,567.89."""
    if value is None:
        return "--"
    neg = value < 0
    n = abs(round(value, decimals))
    whole = int(n)
    frac = n - whole
    s = str(whole)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        s = ",".join(groups) + "," + last3
    if decimals > 0:
        s += "." + f"{frac:.{decimals}f}"[2:]
    prefix = "-" if neg else ("+" if sign and value > 0 else "")
    return prefix + "₹" + s


def fmt_pct(value: Optional[float], sign: bool = True) -> str:
    if value is None:
        return "--"
    p = "+" if sign and value > 0 else ""
    return f"{p}{value:.2f}%"


def pl_color(value: Optional[float]) -> str:
    if value is None or abs(value) < 1e-9:
        return FG
    return GREEN if value > 0 else RED


class MFTrackerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.holdings: list[Holding] = []

        self.title("MF Portfolio Tracker — Indian Mutual Funds")
        self.geometry("1180x740")
        self.minsize(960, 620)
        self.configure(bg=BG)
        self._set_icon()

        self._init_style()
        self._build_header()
        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()

        self.refresh_view()
        # Auto-refresh NAVs on startup if we hold anything.
        if self.db.held_scheme_codes():
            self.after(400, self.refresh_navs)

    def _set_icon(self) -> None:
        # Prefer the multi-resolution .ico on Windows; fall back to PNG.
        try:
            ico = resource_path("icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
                return
        except Exception:
            pass
        try:
            png = resource_path("icon.png")
            if os.path.exists(png):
                self._icon_img = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ----- styling -----------------------------------------------------
    def _init_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=CARD)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 18, "bold"))
        style.configure("TButton", background=CARD, foreground=FG, borderwidth=0, padding=8, font=("Segoe UI", 10))
        style.map("TButton", background=[("active", GRID)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#2563eb")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(16, 8), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])
        style.configure(
            "Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
            rowheight=28, borderwidth=0, font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading", background=BG, foreground=MUTED,
            font=("Segoe UI", 9, "bold"), borderwidth=0,
        )
        style.map("Treeview", background=[("selected", "#24344f")])
        style.configure("TEntry", fieldbackground=CARD, foreground=FG, insertcolor=FG)
        style.configure("TCombobox", fieldbackground=CARD, foreground=FG)

    # ----- header summary cards ---------------------------------------
    def _build_header(self) -> None:
        wrap = ttk.Frame(self, padding=(16, 14, 16, 4))
        wrap.pack(fill="x")
        title = ttk.Label(wrap, text="Portfolio Overview", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w", pady=(0, 10))

        cards = ttk.Frame(wrap)
        cards.pack(fill="x")
        self.card_vars: dict[str, tk.StringVar] = {}
        self.card_value_labels: dict[str, ttk.Label] = {}
        specs = [
            ("invested", "Invested"),
            ("value", "Current Value"),
            ("pl", "Total P&L"),
            ("day", "Day's Change"),
            ("xirr", "XIRR (annualised)"),
        ]
        for i, (key, label) in enumerate(specs):
            cards.columnconfigure(i, weight=1)
            card = ttk.Frame(cards, style="Card.TFrame", padding=14)
            card.grid(row=0, column=i, padx=(0 if i == 0 else 10, 0), sticky="nsew")
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            var = tk.StringVar(value="--")
            val = ttk.Label(card, textvariable=var, style="CardValue.TLabel")
            val.pack(anchor="w", pady=(6, 0))
            self.card_vars[key] = var
            self.card_value_labels[key] = val
            sub = tk.StringVar(value="")
            subl = ttk.Label(card, textvariable=sub, style="Muted.TLabel")
            subl.pack(anchor="w")
            self.card_vars[key + "_sub"] = sub
            self.card_value_labels[key + "_sub"] = subl

    # ----- toolbar -----------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(16, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="+ Add Transaction", style="Accent.TButton",
                   command=self.open_add_dialog).pack(side="left")
        ttk.Button(bar, text="⬇ Import Portfolio (CAS)",
                   command=self.open_import_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="↻ Refresh NAVs",
                   command=self.refresh_navs).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Edit", command=self.edit_selected_txn).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Delete", command=self.delete_selected_txn).pack(side="left", padx=(8, 0))

    # ----- tabs --------------------------------------------------------
    def _build_tabs(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        # Holdings tab
        self.tab_holdings = ttk.Frame(self.nb)
        self.nb.add(self.tab_holdings, text="Holdings")
        cols = ("fund", "units", "avg", "nav", "invested", "value", "pl", "ret", "day", "xirr")
        headings = {
            "fund": "Fund", "units": "Units", "avg": "Avg Cost", "nav": "NAV",
            "invested": "Invested", "value": "Value", "pl": "P&L", "ret": "Return %",
            "day": "Day Chg", "xirr": "XIRR %",
        }
        self.tree_holdings = self._make_tree(self.tab_holdings, cols, headings, "fund")
        self.tree_holdings.column("fund", width=300, anchor="w")
        for c in cols:
            if c != "fund":
                self.tree_holdings.column(c, width=92, anchor="e")
        self.tree_holdings.tag_configure("gain", foreground=GREEN)
        self.tree_holdings.tag_configure("loss", foreground=RED)

        # Transactions tab
        self.tab_txns = ttk.Frame(self.nb)
        self.nb.add(self.tab_txns, text="Transactions")
        tcols = ("date", "fund", "type", "units", "nav", "amount", "notes")
        theadings = {
            "date": "Date", "fund": "Fund", "type": "Type", "units": "Units",
            "nav": "NAV", "amount": "Amount", "notes": "Notes",
        }
        self.tree_txns = self._make_tree(self.tab_txns, tcols, theadings, "fund")
        self.tree_txns.column("date", width=90, anchor="w")
        self.tree_txns.column("fund", width=280, anchor="w")
        self.tree_txns.column("type", width=60, anchor="center")
        for c in ("units", "nav", "amount"):
            self.tree_txns.column(c, width=100, anchor="e")
        self.tree_txns.column("notes", width=160, anchor="w")
        self.tree_txns.tag_configure("buy", foreground=GREEN)
        self.tree_txns.tag_configure("sell", foreground=RED)
        self.tree_txns.bind("<Double-1>", lambda e: self.edit_selected_txn())

        # Allocation tab
        self.tab_alloc = ttk.Frame(self.nb)
        self.nb.add(self.tab_alloc, text="Allocation")
        self.alloc_canvas = tk.Canvas(self.tab_alloc, bg=BG, highlightthickness=0)
        self.alloc_canvas.pack(fill="both", expand=True)
        self.alloc_canvas.bind("<Configure>", lambda e: self._draw_allocation())

        # Insights tab
        self.tab_insights = ttk.Frame(self.nb)
        self.nb.add(self.tab_insights, text="Insights")
        self.insights_text = tk.Text(
            self.tab_insights, bg=CARD, fg=FG, bd=0, padx=18, pady=16,
            font=("Segoe UI", 11), wrap="word", insertbackground=FG,
            spacing1=2, spacing3=6,
        )
        self.insights_text.pack(fill="both", expand=True)
        self.insights_text.tag_configure("h", font=("Segoe UI", 13, "bold"), foreground=ACCENT, spacing3=8)
        self.insights_text.tag_configure("good", foreground=GREEN)
        self.insights_text.tag_configure("bad", foreground=RED)
        self.insights_text.tag_configure("muted", foreground=MUTED)
        self.insights_text.configure(state="disabled")

    def _make_tree(self, parent, cols, headings, stretch_col) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            tree.heading(c, text=headings[c])
            tree.column(c, stretch=(c == stretch_col))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return tree

    # ----- status bar --------------------------------------------------
    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(16, 4))
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel"
                  ).configure(background=BG)
        lbl = ttk.Label(bar, textvariable=self.status_var, foreground=MUTED)
        lbl.pack(side="left")
        ttk.Label(bar, text="Data: AMFI via mfapi.in  •  Not investment advice",
                  foreground=MUTED).pack(side="right")

    def set_status(self, msg: str) -> None:
        self.status_var.set(msg)

    # ----- data refresh ------------------------------------------------
    def refresh_view(self) -> None:
        txns = self.db.get_transactions()
        navs = self.db.get_navs()
        self.holdings = build_holdings(txns, navs)
        self._render_summary()
        self._render_holdings()
        self._render_transactions(txns)
        self._draw_allocation()
        self._render_insights()

    def _render_summary(self) -> None:
        s = summarise(self.holdings)
        self.card_vars["invested"].set(fmt_inr(s.invested))
        self.card_vars["invested_sub"].set(
            f"{len([h for h in self.holdings if h.units > 1e-9])} funds held")
        self.card_vars["value"].set(fmt_inr(s.current_value))
        self.card_vars["value_sub"].set(
            "some NAVs missing — refresh" if s.has_unpriced else "live valuation")

        self.card_vars["pl"].set(fmt_inr(s.unrealised_pl, sign=True))
        self.card_value_labels["pl"].configure(foreground=pl_color(s.unrealised_pl))
        self.card_vars["pl_sub"].set(fmt_pct(s.return_pct))

        self.card_vars["day"].set(fmt_inr(s.day_change, sign=True))
        self.card_value_labels["day"].configure(foreground=pl_color(s.day_change))
        self.card_vars["day_sub"].set(fmt_pct(s.day_change_pct))

        self.card_vars["xirr"].set(fmt_pct(s.xirr_pct))
        self.card_value_labels["xirr"].configure(foreground=pl_color(s.xirr_pct))
        self.card_vars["xirr_sub"].set(
            f"Realised: {fmt_inr(s.realised_pl, sign=True)}" if abs(s.realised_pl) > 0.5 else "money-weighted")

    def _render_holdings(self) -> None:
        self.tree_holdings.delete(*self.tree_holdings.get_children())
        for h in self.holdings:
            if h.units <= 1e-9:
                continue
            pl = h.unrealised_pl
            tag = "gain" if (pl or 0) > 0 else ("loss" if (pl or 0) < 0 else "")
            self.tree_holdings.insert(
                "", "end", iid=h.scheme_code,
                values=(
                    h.scheme_name,
                    f"{h.units:,.3f}",
                    fmt_inr(h.avg_cost),
                    fmt_inr(h.nav) if h.nav else "--",
                    fmt_inr(h.invested),
                    fmt_inr(h.current_value),
                    fmt_inr(pl, sign=True),
                    fmt_pct(h.return_pct),
                    fmt_inr(h.day_change, sign=True),
                    fmt_pct(h.xirr_pct),
                ),
                tags=(tag,),
            )

    def _render_transactions(self, txns: list[Transaction]) -> None:
        self.tree_txns.delete(*self.tree_txns.get_children())
        for t in sorted(txns, key=lambda x: (x.txn_date, x.id), reverse=True):
            tag = "buy" if t.txn_type == "BUY" else "sell"
            self.tree_txns.insert(
                "", "end", iid=str(t.id),
                values=(
                    t.txn_date, t.scheme_name, t.txn_type,
                    f"{t.units:,.3f}", fmt_inr(t.nav), fmt_inr(t.amount), t.notes,
                ),
                tags=(tag,),
            )

    # ----- allocation pie chart ---------------------------------------
    def _draw_allocation(self) -> None:
        c = self.alloc_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            return
        priced = [(hd.scheme_name, hd.current_value) for hd in self.holdings
                  if hd.current_value and hd.current_value > 0]
        if not priced:
            c.create_text(w // 2, h // 2, text="No priced holdings yet.\nAdd transactions and refresh NAVs.",
                          fill=MUTED, font=("Segoe UI", 12), justify="center")
            return
        priced.sort(key=lambda x: x[1], reverse=True)
        total = sum(v for _, v in priced)

        c.create_text(20, 24, text="Allocation by current value", anchor="w",
                      fill=FG, font=("Segoe UI", 13, "bold"))

        cx, cy = min(w * 0.30, 320), h * 0.55
        r = min(cx - 40, h * 0.34)
        start = 90.0
        for i, (name, val) in enumerate(priced):
            extent = -(val / total) * 360.0
            color = PIE_COLORS[i % len(PIE_COLORS)]
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=extent,
                         fill=color, outline=BG, width=2, style="pieslice")
            start += extent
        # donut hole
        hr = r * 0.55
        c.create_oval(cx - hr, cy - hr, cx + hr, cy + hr, fill=BG, outline=BG)
        c.create_text(cx, cy - 10, text="Total", fill=MUTED, font=("Segoe UI", 10))
        c.create_text(cx, cy + 12, text=fmt_inr(total, 0), fill=FG, font=("Segoe UI", 13, "bold"))

        # legend
        lx = cx + r + 50
        ly = 70
        for i, (name, val) in enumerate(priced):
            if ly > h - 30:
                break
            color = PIE_COLORS[i % len(PIE_COLORS)]
            c.create_rectangle(lx, ly, lx + 14, ly + 14, fill=color, outline=color)
            pct = val / total * 100
            label = name if len(name) <= 46 else name[:43] + "..."
            c.create_text(lx + 24, ly + 7, anchor="w", fill=FG, font=("Segoe UI", 10),
                          text=f"{label}")
            c.create_text(lx + 24, ly + 22, anchor="w", fill=MUTED, font=("Segoe UI", 9),
                          text=f"{fmt_inr(val, 0)}  •  {pct:.1f}%")
            ly += 44

    # ----- insights ----------------------------------------------------
    def _render_insights(self) -> None:
        t = self.insights_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        held = [h for h in self.holdings if h.units > 1e-9]
        if not held:
            t.insert("end", "No holdings yet.\n\n", "muted")
            t.insert("end", "Click “+ Add Transaction”, search for your fund by name, "
                            "enter the amount/units you invested, and the app will pull the "
                            "live NAV to value your portfolio.\n", "muted")
            t.configure(state="disabled")
            return

        s = summarise(self.holdings)
        ranked = sorted([h for h in held if h.return_pct is not None],
                        key=lambda x: x.return_pct, reverse=True)

        t.insert("end", "Performance leaders\n", "h")
        for h in ranked[:3]:
            tag = "good" if h.return_pct > 0 else "bad"
            t.insert("end", f"  ↑ {h.scheme_name}\n")
            t.insert("end", f"      {fmt_pct(h.return_pct)}  ({fmt_inr(h.unrealised_pl, sign=True)})\n", tag)

        if len(ranked) > 1:
            t.insert("end", "\nNeeds attention\n", "h")
            for h in ranked[-3:][::-1]:
                if h.return_pct >= 0 and ranked[0].return_pct == h.return_pct:
                    continue
                tag = "good" if h.return_pct > 0 else "bad"
                t.insert("end", f"  ↓ {h.scheme_name}\n")
                t.insert("end", f"      {fmt_pct(h.return_pct)}  ({fmt_inr(h.unrealised_pl, sign=True)})\n", tag)

        # Concentration check
        t.insert("end", "\nDiversification\n", "h")
        priced = [(h.scheme_name, h.current_value) for h in held if h.current_value]
        if priced and s.priced_value > 0:
            priced.sort(key=lambda x: x[1], reverse=True)
            top_name, top_val = priced[0]
            top_pct = top_val / s.priced_value * 100
            t.insert("end", f"  • {len(held)} funds held.\n")
            line = f"  • Largest position: {top_name} at {top_pct:.1f}% of portfolio.\n"
            t.insert("end", line, "bad" if top_pct > 40 else None)
            if top_pct > 40:
                t.insert("end", "      High concentration — consider rebalancing.\n", "muted")
            elif len(held) < 4:
                t.insert("end", "      Few funds — a more diversified mix may reduce risk.\n", "muted")
            else:
                t.insert("end", "      Reasonably diversified.\n", "muted")

        t.insert("end", "\nOverall\n", "h")
        t.insert("end", f"  • Invested {fmt_inr(s.invested)}, now worth {fmt_inr(s.current_value)}.\n")
        t.insert("end", f"  • Total return {fmt_pct(s.return_pct)}",
                 "good" if (s.return_pct or 0) >= 0 else "bad")
        t.insert("end", f", annualised (XIRR) {fmt_pct(s.xirr_pct)}.\n",
                 "good" if (s.xirr_pct or 0) >= 0 else "bad")
        if s.xirr_pct is not None:
            if s.xirr_pct < 6:
                t.insert("end", "      Below typical FD returns — review underperformers.\n", "muted")
            elif s.xirr_pct > 12:
                t.insert("end", "      Strong annualised return.\n", "muted")
        t.insert("end", "\nThis is informational only, not investment advice.\n", "muted")
        t.configure(state="disabled")

    # ----- NAV refresh (threaded) -------------------------------------
    def refresh_navs(self) -> None:
        # Skip placeholder codes from unmatched CAS schemes (no live NAV).
        codes = [(c, n) for c, n in self.db.held_scheme_codes() if c.isdigit()]
        if not codes:
            self.set_status("Nothing to refresh — add a transaction first.")
            return
        self.set_status(f"Refreshing NAVs for {len(codes)} fund(s)…")
        threading.Thread(target=self._refresh_navs_worker, args=(codes,), daemon=True).start()

    def _refresh_navs_worker(self, codes) -> None:
        ok, fail = 0, 0
        for code, _name in codes:
            try:
                q = api.fetch_nav(code)
                self.db.upsert_nav(q)
                ok += 1
            except api.ApiError:
                fail += 1
        msg = f"Updated {ok} NAV(s)" + (f", {fail} failed" if fail else "")
        self.after(0, lambda: self._after_refresh(msg))

    def _after_refresh(self, msg: str) -> None:
        self.refresh_view()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.set_status(f"{msg}  •  last updated {stamp}")

    # ----- transaction CRUD -------------------------------------------
    def open_add_dialog(self) -> None:
        TransactionDialog(self, on_save=self._save_new_txn)

    def open_import_dialog(self) -> None:
        CasImportDialog(self, on_import=self._commit_import)

    def _commit_import(self, parsed: list, skip_duplicates: bool) -> tuple[int, int]:
        added, skipped = self.db.import_transactions(parsed, skip_duplicates=skip_duplicates)
        self.refresh_view()
        if added:
            # Value the newly imported funds right away.
            self.refresh_navs()
        self.set_status(f"Imported {added} transaction(s); skipped {skipped} duplicate(s).")
        return added, skipped

    def _save_new_txn(self, data: dict) -> None:
        self.db.add_transaction(**data)
        # Pull a fresh NAV for the new scheme so it values immediately.
        threading.Thread(
            target=self._refresh_navs_worker,
            args=([(data["scheme_code"], data["scheme_name"])],),
            daemon=True,
        ).start()
        self.refresh_view()
        self.set_status(f"Added {data['txn_type']}: {data['scheme_name']}")

    def _selected_txn(self) -> Optional[Transaction]:
        sel = self.tree_txns.selection()
        if not sel:
            return None
        txn_id = int(sel[0])
        for t in self.db.get_transactions():
            if t.id == txn_id:
                return t
        return None

    def edit_selected_txn(self) -> None:
        self.nb.select(self.tab_txns)
        txn = self._selected_txn()
        if not txn:
            messagebox.showinfo("Edit transaction", "Select a transaction in the Transactions tab first.")
            return
        TransactionDialog(self, on_save=self._save_edit_txn, existing=txn)

    def _save_edit_txn(self, data: dict, txn_id: int) -> None:
        self.db.update_transaction(Transaction(id=txn_id, **data))
        self.refresh_view()
        self.set_status("Transaction updated.")

    def delete_selected_txn(self) -> None:
        self.nb.select(self.tab_txns)
        txn = self._selected_txn()
        if not txn:
            messagebox.showinfo("Delete transaction", "Select a transaction in the Transactions tab first.")
            return
        if messagebox.askyesno(
            "Delete transaction",
            f"Delete this {txn.txn_type} of {txn.scheme_name}?\n"
            f"{txn.txn_date}  •  {fmt_inr(txn.amount)}",
        ):
            self.db.delete_transaction(txn.id)
            self.refresh_view()
            self.set_status("Transaction deleted.")


class TransactionDialog(tk.Toplevel):
    """Add or edit a transaction, with live fund search and NAV prefill."""

    def __init__(self, parent: MFTrackerApp, on_save, existing: Optional[Transaction] = None):
        super().__init__(parent)
        self.parent = parent
        self.on_save = on_save
        self.existing = existing
        self.scheme_code: Optional[str] = existing.scheme_code if existing else None
        self.scheme_name: Optional[str] = existing.scheme_name if existing else None
        self._search_results: list[api.Scheme] = []
        self._search_job = None

        self.title("Edit Transaction" if existing else "Add Transaction")
        self.configure(bg=BG)
        self.geometry("520x600")
        self.transient(parent)
        self.grab_set()

        self._build()
        if existing:
            self._load_existing()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self) -> None:
        pad = {"padx": 18}
        ttk.Label(self, text="Search fund by name", style="Muted.TLabel"
                  ).configure(background=BG)
        head = ttk.Label(self, text="Fund", font=("Segoe UI", 11, "bold"))
        head.pack(anchor="w", pady=(16, 4), **pad)

        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(self, textvariable=self.search_var, font=("Segoe UI", 11))
        self.search_entry.pack(fill="x", **pad)
        self.search_var.trace_add("write", lambda *a: self._on_search_change())

        self.results_list = tk.Listbox(
            self, height=6, bg=CARD, fg=FG, bd=0, highlightthickness=1,
            highlightbackground=GRID, selectbackground=ACCENT, font=("Segoe UI", 10),
            activestyle="none",
        )
        self.results_list.pack(fill="x", pady=(6, 0), **pad)
        self.results_list.bind("<<ListboxSelect>>", self._on_pick_scheme)

        self.selected_var = tk.StringVar(value="No fund selected")
        ttk.Label(self, textvariable=self.selected_var, foreground=ACCENT,
                  font=("Segoe UI", 10, "bold"), wraplength=480, justify="left"
                  ).pack(anchor="w", pady=(10, 0), **pad)

        # form grid
        form = ttk.Frame(self, padding=(18, 16, 18, 8))
        form.pack(fill="x")
        for i in range(2):
            form.columnconfigure(i, weight=1)

        ttk.Label(form, text="Type").grid(row=0, column=0, sticky="w", pady=4)
        self.type_var = tk.StringVar(value="BUY")
        type_cb = ttk.Combobox(form, textvariable=self.type_var, state="readonly",
                               values=["BUY", "SELL"])
        type_cb.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(form, text="Date (YYYY-MM-DD)").grid(row=0, column=1, sticky="w", pady=4)
        self.date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(form, textvariable=self.date_var).grid(row=1, column=1, sticky="ew")

        ttk.Label(form, text="NAV (₹ per unit)").grid(row=2, column=0, sticky="w", pady=(12, 4))
        self.nav_var = tk.StringVar()
        nav_e = ttk.Entry(form, textvariable=self.nav_var)
        nav_e.grid(row=3, column=0, sticky="ew", padx=(0, 8))

        self.fetch_btn = ttk.Button(form, text="Use live NAV", command=self._fetch_live_nav)
        self.fetch_btn.grid(row=3, column=1, sticky="ew")

        ttk.Label(form, text="Units").grid(row=4, column=0, sticky="w", pady=(12, 4))
        self.units_var = tk.StringVar()
        ue = ttk.Entry(form, textvariable=self.units_var)
        ue.grid(row=5, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(form, text="Amount (₹)").grid(row=4, column=1, sticky="w", pady=(12, 4))
        self.amount_var = tk.StringVar()
        ae = ttk.Entry(form, textvariable=self.amount_var)
        ae.grid(row=5, column=1, sticky="ew")

        ttk.Label(form, text="Enter any two of NAV / Units / Amount — the third is computed.",
                  foreground=MUTED, font=("Segoe UI", 9)).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(form, text="Notes (optional)").grid(row=7, column=0, sticky="w", pady=(12, 4))
        self.notes_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.notes_var).grid(
            row=8, column=0, columnspan=2, sticky="ew")

        self.dlg_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.dlg_status, foreground=MUTED,
                  wraplength=480, justify="left").pack(anchor="w", pady=(4, 0), padx=18)

        btns = ttk.Frame(self, padding=(18, 10))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self._save
                   ).pack(side="right", padx=(0, 8))

    def _load_existing(self) -> None:
        e = self.existing
        self.selected_var.set(e.scheme_name)
        self.search_var.set(e.scheme_name)
        self.type_var.set(e.txn_type)
        self.date_var.set(e.txn_date)
        self.nav_var.set(f"{e.nav:g}")
        self.units_var.set(f"{e.units:g}")
        self.amount_var.set(f"{e.amount:g}")
        self.notes_var.set(e.notes)

    # ----- fund search (debounced, threaded) --------------------------
    def _on_search_change(self) -> None:
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(350, self._run_search)

    def _run_search(self) -> None:
        q = self.search_var.get().strip()
        if len(q) < 3:
            self.results_list.delete(0, "end")
            return
        self.dlg_status.set("Searching…")
        threading.Thread(target=self._search_worker, args=(q,), daemon=True).start()

    def _search_worker(self, q: str) -> None:
        try:
            results = api.search_schemes(q, limit=40)
            err = None
        except api.ApiError as e:
            results, err = [], str(e)
        self.after(0, lambda: self._show_results(results, err))

    def _show_results(self, results, err) -> None:
        self._search_results = results
        self.results_list.delete(0, "end")
        if err:
            self.dlg_status.set(err)
            return
        for s in results:
            self.results_list.insert("end", s.name)
        self.dlg_status.set(f"{len(results)} match(es)" if results else "No matches")

    def _on_pick_scheme(self, _event) -> None:
        sel = self.results_list.curselection()
        if not sel:
            return
        s = self._search_results[sel[0]]
        self.scheme_code = s.code
        self.scheme_name = s.name
        self.selected_var.set(f"✓ {s.name}  (code {s.code})")
        if self.type_var.get() == "BUY" and not self.nav_var.get():
            self._fetch_live_nav()

    def _fetch_live_nav(self) -> None:
        if not self.scheme_code:
            self.dlg_status.set("Select a fund first.")
            return
        self.dlg_status.set("Fetching live NAV…")
        threading.Thread(target=self._fetch_nav_worker, args=(self.scheme_code,), daemon=True).start()

    def _fetch_nav_worker(self, code) -> None:
        try:
            q = api.fetch_nav(code)
            self.after(0, lambda: self._apply_live_nav(q.nav, q.nav_date))
        except api.ApiError as e:
            self.after(0, lambda: self.dlg_status.set(str(e)))

    def _apply_live_nav(self, nav: float, nav_date: str) -> None:
        self.nav_var.set(f"{nav:g}")
        self.dlg_status.set(f"Live NAV ₹{nav:g} as of {nav_date}. "
                            f"Now enter units or amount.")
        self._recompute()

    # ----- compute missing field --------------------------------------
    def _recompute(self) -> None:
        nav = _to_float(self.nav_var.get())
        units = _to_float(self.units_var.get())
        amount = _to_float(self.amount_var.get())
        if nav and units and not amount:
            self.amount_var.set(f"{nav * units:.2f}")
        elif nav and amount and not units:
            self.units_var.set(f"{amount / nav:.4f}")

    def _save(self) -> None:
        if not self.scheme_code:
            self.dlg_status.set("Please select a fund.")
            return
        self._recompute()
        nav = _to_float(self.nav_var.get())
        units = _to_float(self.units_var.get())
        amount = _to_float(self.amount_var.get())

        # Fill the third value if two are present.
        if nav and units and not amount:
            amount = nav * units
        elif nav and amount and not units:
            units = amount / nav
        elif units and amount and not nav:
            nav = amount / units if units else 0

        if not (nav and units and amount):
            self.dlg_status.set("Enter at least two of NAV / Units / Amount.")
            return

        try:
            datetime.strptime(self.date_var.get().strip(), "%Y-%m-%d")
        except ValueError:
            self.dlg_status.set("Date must be in YYYY-MM-DD format.")
            return

        data = dict(
            scheme_code=self.scheme_code,
            scheme_name=self.scheme_name,
            txn_type=self.type_var.get(),
            txn_date=self.date_var.get().strip(),
            units=round(units, 4),
            nav=round(nav, 4),
            amount=round(amount, 2),
            notes=self.notes_var.get().strip(),
        )
        if self.existing:
            self.on_save(data, self.existing.id)
        else:
            self.on_save(data)
        self.destroy()


class CasImportDialog(tk.Toplevel):
    """Pick a CAMS/KFintech CAS PDF, parse it, preview, and import."""

    def __init__(self, parent: MFTrackerApp, on_import):
        super().__init__(parent)
        self.parent = parent
        self.on_import = on_import
        self.result: Optional[cas_import.CasResult] = None

        self.title("Import Portfolio from CAS")
        self.configure(bg=BG)
        self.geometry("820x640")
        self.transient(parent)
        self.grab_set()

        self._build()
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self._check_dependencies)

    def _build(self) -> None:
        pad = {"padx": 18}
        ttk.Label(self, text="Import from CAS PDF", font=("Segoe UI", 14, "bold")
                  ).pack(anchor="w", pady=(16, 2), **pad)
        ttk.Label(
            self,
            text="Use the CAMS/KFintech Consolidated Account Statement (CAS). "
                 "Request a DETAILED statement from camsonline.com or kfintech.com "
                 "for full transaction history and accurate returns.",
            foreground=MUTED, wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, 12), **pad)

        # dependency banner
        self.dep_var = tk.StringVar(value="")
        self.dep_frame = ttk.Frame(self, style="Card.TFrame", padding=10)
        self.dep_label = ttk.Label(self.dep_frame, textvariable=self.dep_var,
                                   style="Card.TLabel", wraplength=620, justify="left")
        self.dep_label.pack(side="left", fill="x", expand=True)
        self.install_btn = ttk.Button(self.dep_frame, text="Install now",
                                      style="Accent.TButton", command=self._install_deps)

        # file row
        frm = ttk.Frame(self, padding=(18, 4, 18, 4))
        frm.pack(fill="x")
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="CAS PDF file").grid(row=0, column=0, sticky="w", pady=4)
        self.path_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.path_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(frm, text="Browse…", command=self._browse).grid(row=0, column=2)

        ttk.Label(frm, text="Password").grid(row=1, column=0, sticky="w", pady=4)
        self.pwd_var = tk.StringVar()
        self.pwd_entry = ttk.Entry(frm, textvariable=self.pwd_var, show="•")
        self.pwd_entry.grid(row=1, column=1, sticky="ew", padx=8)
        self.show_pwd = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Show", variable=self.show_pwd,
                        command=self._toggle_pwd).grid(row=1, column=2, sticky="w")
        ttk.Label(frm, text="Usually your PAN in uppercase (e.g. ABCDE1234F), "
                            "or the password you set when requesting the CAS.",
                  foreground=MUTED, font=("Segoe UI", 9)).grid(
            row=2, column=1, columnspan=2, sticky="w")

        self.parse_btn = ttk.Button(frm, text="Parse statement", style="Accent.TButton",
                                    command=self._parse)
        self.parse_btn.grid(row=3, column=1, sticky="w", padx=8, pady=(10, 4))

        self.summary_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.summary_var, foreground=ACCENT,
                  wraplength=760, justify="left").pack(anchor="w", pady=(6, 4), **pad)

        # preview table
        prev = ttk.Frame(self, padding=(18, 0))
        prev.pack(fill="both", expand=True)
        cols = ("fund", "type", "date", "units", "nav", "amount")
        self.tree = ttk.Treeview(prev, columns=cols, show="headings", height=10)
        for c, t, w, a in [
            ("fund", "Fund", 300, "w"), ("type", "Type", 60, "center"),
            ("date", "Date", 90, "w"), ("units", "Units", 90, "e"),
            ("nav", "NAV", 90, "e"), ("amount", "Amount", 110, "e"),
        ]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "fund"))
        vsb = ttk.Scrollbar(prev, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("buy", foreground=GREEN)
        self.tree.tag_configure("sell", foreground=RED)
        self.tree.tag_configure("unmatched", foreground=MUTED)

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground=MUTED,
                  wraplength=760, justify="left").pack(anchor="w", pady=(4, 0), **pad)

        btns = ttk.Frame(self, padding=(18, 10))
        btns.pack(fill="x", side="bottom")
        self.skip_dupes = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="Skip transactions already imported",
                        variable=self.skip_dupes).pack(side="left")
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        self.import_btn = ttk.Button(btns, text="Import", style="Accent.TButton",
                                     command=self._do_import, state="disabled")
        self.import_btn.pack(side="right", padx=(0, 8))

    # ----- dependency handling ----------------------------------------
    def _check_dependencies(self) -> None:
        missing = cas_import.missing_packages()
        if missing:
            self.dep_frame.pack(fill="x", padx=18, pady=(0, 8))
            self.dep_var.set(
                "Reading CAS PDFs needs an extra component: "
                + ", ".join(missing) + ". Click ‘Install now’ (one-time)."
            )
            self.install_btn.pack(side="right")
            self.parse_btn.configure(state="disabled")
        else:
            self.dep_frame.pack_forget()
            self.parse_btn.configure(state="normal")

    def _install_deps(self) -> None:
        self.dep_var.set("Installing… this may take a minute.")
        self.install_btn.configure(state="disabled")
        threading.Thread(target=self._install_worker, daemon=True).start()

    def _install_worker(self) -> None:
        import subprocess
        import sys
        pkgs = cas_import.missing_packages()
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", *pkgs],
                check=True, capture_output=True, text=True,
            )
            ok, msg = True, "Installed. You can parse your statement now."
        except subprocess.CalledProcessError as e:
            ok, msg = False, f"Install failed: {(e.stderr or e.stdout or '').strip()[:300]}"
        except Exception as e:  # pip missing, no network, etc.
            ok, msg = False, f"Install failed: {e}"
        self.after(0, lambda: self._after_install(ok, msg))

    def _after_install(self, ok: bool, msg: str) -> None:
        self.dep_var.set(msg)
        self.install_btn.configure(state="normal")
        if ok:
            self._check_dependencies()

    # ----- file + parse -----------------------------------------------
    def _browse(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select CAS PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)

    def _toggle_pwd(self) -> None:
        self.pwd_entry.configure(show="" if self.show_pwd.get() else "•")

    def _parse(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            self.status_var.set("Choose a CAS PDF file first.")
            return
        self.status_var.set("Parsing statement…")
        self.parse_btn.configure(state="disabled")
        self.import_btn.configure(state="disabled")
        threading.Thread(target=self._parse_worker,
                         args=(path, self.pwd_var.get()), daemon=True).start()

    def _parse_worker(self, path: str, pwd: str) -> None:
        try:
            result = cas_import.parse_cas(path, pwd)
            self.after(0, lambda: self._show_result(result))
        except cas_import.CasImportError as e:
            self.after(0, lambda: self._parse_failed(str(e)))
        except Exception as e:  # defensive: never crash the dialog
            self.after(0, lambda: self._parse_failed(f"Unexpected error: {e}"))

    def _parse_failed(self, msg: str) -> None:
        self.parse_btn.configure(state="normal")
        self.status_var.set(msg)

    def _show_result(self, result: cas_import.CasResult) -> None:
        self.result = result
        self.parse_btn.configure(state="normal")
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(result.transactions):
            tag = "unmatched" if not t.matched else ("buy" if t.txn_type == "BUY" else "sell")
            self.tree.insert("", "end", iid=str(i), values=(
                t.scheme_name, t.txn_type, t.txn_date,
                f"{t.units:,.3f}", fmt_inr(t.nav), fmt_inr(t.amount),
            ), tags=(tag,))

        kind = f"{result.file_type} {result.cas_type}".strip()
        period = (f"{result.period_from} → {result.period_to}"
                  if result.period_from else "")
        noun = "holdings" if result.is_depository else "transactions"
        self.summary_var.set(
            f"{kind} CAS  •  {len(result.transactions)} {noun}  •  "
            f"{result.scheme_count} schemes across {result.folio_count} folios"
            + (f"  •  {period}" if period else "")
        )

        notes = []
        if result.is_depository:
            notes.append("ℹ NSDL/CDSL statement: this lists current holdings only, "
                         "not individual transactions. Each fund is imported as one "
                         "opening BUY — value & P&L are accurate, but XIRR can't be "
                         "computed. For annualised XIRR, import a DETAILED CAMS/KFintech "
                         "CAS (camsonline.com / kfintech.com, 'with transactions').")
        elif result.is_summary:
            notes.append("ℹ Summary statement: opening balances imported as a single "
                         "BUY each. Value & P&L are accurate, but XIRR needs a DETAILED "
                         "CAS (request one with full transaction history).")
        if result.unmatched_schemes:
            notes.append(f"{len(result.unmatched_schemes)} scheme(s) had no AMFI code "
                         "and won't fetch live NAVs: "
                         + ", ".join(result.unmatched_schemes[:3])
                         + ("…" if len(result.unmatched_schemes) > 3 else ""))
        if result.warnings:
            notes.append("Parser notes: " + "; ".join(result.warnings[:2]))
        self.status_var.set("  ".join(notes) if notes else "Looks good. Review and click Import.")

        self.import_btn.configure(state=("normal" if result.transactions else "disabled"))

    def _do_import(self) -> None:
        if not self.result or not self.result.transactions:
            return
        added, skipped = self.on_import(self.result.transactions, self.skip_dupes.get())
        messagebox.showinfo(
            "Import complete",
            f"Imported {added} transaction(s).\nSkipped {skipped} duplicate(s).",
            parent=self,
        )
        self.destroy()


def _to_float(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def main() -> None:
    app = MFTrackerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
