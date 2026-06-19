# MF Portfolio Tracker

A self-contained Windows desktop app to track your **Indian mutual fund
portfolio** and make better decisions. It pulls **live NAVs** from the official
AMFI India feed (via the free, no-key `mfapi.in` mirror) and computes returns,
day change, XIRR, allocation and plain-English insights.

The core app runs on the Python standard library (`tkinter` + `sqlite3`); the
CAS-import feature additionally uses `casparser` + `pymupdf` (both bundled into
the prebuilt installer).

---

## Download & install (Windows)

1. Go to the [**Releases**](../../releases/latest) page.
2. Download **`MFPortfolioTracker-Setup-x.y.z.exe`**.
3. Run it. It installs per-user (no admin needed) and adds a Start-menu shortcut.

> **SmartScreen / "Windows protected your PC":** the installer is not
> code-signed, so Windows may warn the first time. Click **More info → Run
> anyway**. If **Smart App Control** blocks it, right-click the downloaded file →
> **Properties → Unblock → OK**, then run it. This is expected for unsigned
> open-source software; the code is fully readable in this repo.

A portable build (`MF Portfolio Tracker.exe`, no install) is also attached to
each release.

---

## Run from source

```powershell
git clone <repo-url>
cd mf-portfolio-tracker
pip install -r requirements.txt   # only needed for CAS import
python main.py
```

Requires Python 3.10+ with Tkinter (the standard python.org installer includes
it). The core tracker works without `pip install`; CAS import will prompt to
install its two dependencies on first use.

---

## Build the installer yourself

```powershell
pip install pyinstaller pillow
python make_icon.py                       # regenerate icon.ico
pyinstaller --noconfirm MFPortfolioTracker.spec
# then compile the installer with Inno Setup 6:
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

Outputs the portable exe to `dist\` and the installer to `installer_output\`.

---

## What it does

### Portfolio overview (top cards)
- **Invested** — total cost of units you still hold
- **Current Value** — live valuation at the latest NAV
- **Total P&L** — unrealised gain/loss and return %
- **Day's Change** — value moved since the previous NAV
- **XIRR** — money-weighted annualised return across all your dated cashflows

### Tabs
- **Holdings** — per-fund units, average cost, live NAV, value, P&L, return %, day change, XIRR
- **Transactions** — full ledger of every buy/sell (add / edit / delete)
- **Allocation** — donut chart of how your money is split across funds, with a legend
- **Insights** — top/bottom performers, concentration warnings, and an overall read on your portfolio

---

## Auto-import your existing portfolio (CAS)

Rather than entering everything by hand, you can pull your **entire** portfolio
from a **CAMS/KFintech Consolidated Account Statement (CAS)** — one PDF that
covers every AMC you've ever invested in.

1. Get the statement: go to **camsonline.com** (or **kfintech.com**) →
   *Statements → Consolidated Account Statement* → choose **Detailed** (with
   transactions), pick the period (e.g. "Since Inception"), and enter your email.
   You'll receive a **password-protected PDF**.
2. In the app, click **⬇ Import Portfolio (CAS)**.
3. The first time, click **Install now** — this fetches the one-time PDF-reading
   components (`casparser` + `pymupdf`).
4. **Browse** to the PDF, type the **password** (usually your PAN in uppercase),
   and click **Parse statement**.
5. Review the preview of every detected buy/sell, then click **Import**.

How it's interpreted:
- Each CAS row is mapped to **BUY** or **SELL** by the direction of units, so
  purchases, SIPs, switches, merges and dividend-reinvestments are all handled.
- Pure cash/tax rows (dividend payout, STT, stamp duty, TDS) are skipped.
- Re-importing a newer CAS is safe — **duplicates are skipped** automatically.
- A **Summary** CAS (no transaction list) still imports your current holdings as
  opening balances; use a **Detailed** CAS for accurate XIRR.

> Tip: the import components are optional. The app runs fine without them — it
> just prompts you to install them the first time you open the import dialog.

## How to use (manual entry)

1. Click **+ Add Transaction**.
2. Type at least 3 letters of the fund name (e.g. `sbi small cap`) and pick it
   from the live search list.
3. Choose **BUY** or **SELL** and a date. The app auto-fills the **live NAV** for
   buys — you can override it with your actual purchase NAV.
4. Enter **any two** of NAV / Units / Amount; the third is computed automatically.
5. **Save.** The fund is valued immediately, and the dashboard updates.

Use **↻ Refresh NAVs** any time to re-fetch the latest NAVs for all your funds
(also happens automatically on startup).

### Buys, sells and returns
- Holdings use **average-cost basis**. A SELL books realised P&L against your
  average cost and reduces the units/invested figures for that fund.
- **XIRR** treats every BUY as money out and every SELL plus your current value
  as money in, giving a true annualised, date-aware return.

---

## Where your data lives

Everything is stored locally in:

```
%USERPROFILE%\.mf_tracker\
  portfolio.db        <- your transactions + cached NAVs (SQLite)
  scheme_list.json    <- cached fund master list (refreshed daily)
```

Nothing is uploaded anywhere. The app only makes outbound calls to `mfapi.in`
to read NAVs.

---

## Project layout

```
mf_tracker/
  __init__.py
  __main__.py     entry point (python -m mf_tracker)
  db.py           SQLite storage (transactions + NAV cache, dedup import)
  api.py          live NAV / fund-search client (AMFI via mfapi.in)
  cas_import.py   CAMS/KFintech CAS PDF importer (optional deps)
  finance.py      holdings, returns, day change, XIRR analytics
  app.py          Tkinter GUI
MF Portfolio Tracker.bat   double-click launcher
```

---

## Notes & disclaimer

- NAVs are end-of-day (mutual funds don't trade intraday), so "Day's Change"
  reflects the latest published NAV vs. the previous one.
- Requires an internet connection to fetch NAVs and search funds; cached data
  keeps it usable offline.
- This tool is for **informational purposes only and is not investment advice.**
