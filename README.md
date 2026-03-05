# Housemartin Returns Automation

Automates the full pipeline for a family Housemartin portfolio: logs into all accounts, scrapes balances, downloads transaction CSVs, merges them, calculates XIRR, and produces an HTML report plus snapshot files for historical tracking.

> **Disclaimer:** This is independent personal automation with no affiliation with Housemartin Property Limited. It is not financial advice. Use at your own risk.

> **Fragility notice:** The scraper works by interacting with Housemartin's web interface directly — it depends on the site's current HTML structure, CSS class names, and Angular component layout. If Housemartin updates their frontend, the scraper may break and will need its selectors updated. See [Troubleshooting](#troubleshooting) for guidance on fixing selectors.

---

## File layout

Put all files in the same folder:

```
housemartin/
├── run.py                  ← main orchestrator — run this
├── housemartin_scraper.py  ← Playwright: login, balance scrape, CSV download
├── merge_csv.py            ← merges per-account CSVs + injects current value
├── irr5.py                 ← XIRR / IRR calculations
├── report_generator.py     ← HTML report builder
├── .env                    ← your credentials (never commit this)
└── .env.example            ← template — copy to .env and fill in
```

Generated at runtime:

```
├── ReportsTransactionAll.csv       ← merged transaction history
├── hm_staging/                     ← per-account CSVs + debug screenshots
├── reports/
│   ├── hm_report_YYYYMMDD_HHMMSS.html   ← timestamped HTML report
│   └── hm_report_latest.html            ← always the most recent (auto-opens)
└── snapshots/
    ├── hm_snapshot_YYYYMMDD_HHMMSS.txt  ← plain-text performance snapshot
    └── hm_history.csv                   ← one row per run, grows over time
```

---

## Setup (one-time)

### 1. Install Python dependencies

```bash
pip install playwright pandas numpy scipy yfinance python-dotenv
playwright install chromium
```

### 2. Configure credentials

```bash
copy .env.example .env       # Windows
# cp .env.example .env       # Mac/Linux
```

Open `.env` and fill in your details:

```ini
HM_ACCOUNT1_NAME=Name1
HM_ACCOUNT1_EMAIL=you@example.com
HM_ACCOUNT1_PASSWORD=yourpassword

HM_ACCOUNT2_NAME=Name2
HM_ACCOUNT2_EMAIL=partner@example.com
HM_ACCOUNT2_PASSWORD=partnerpassword

HM_ACCOUNT3_NAME=Name3
HM_ACCOUNT3_EMAIL=child@example.com
HM_ACCOUNT3_PASSWORD=childpassword

# Benchmark ETFs — comma-separated Yahoo Finance tickers
# Defaults: Global Equities, European Property, Corp Bonds
ETF_TICKERS=VWRP.L,XDER.L,SLXX.L
```

Accounts with blank credentials are automatically skipped — if you only have two accounts, leave the third block empty.

---

## Usage

### Normal run (headless — no browser window):
```bash
python run.py
```

### Debug run (browser visible — use this if something breaks):
```bash
python run.py --visible
```

### Skip scraping (reuse CSVs already in `hm_staging/`):
```bash
python run.py --skip-scrape
```

---

## What each step does

| Step | Script | What happens |
|------|--------|--------------|
| 1 | `housemartin_scraper.py` | Logs into each account, reads the dashboard balance (including any withdrawal requests awaiting authorisation), selects All accounts in the transaction filter, and downloads the full CSV |
| 2 | `merge_csv.py` | Merges per-account CSVs, injects the total current balance as a "Current Value" row in the Cash change column (required for XIRR) |
| 3 | `irr5.py` | Calculates XIRR for the portfolio and for each benchmark ETF using the same cash flow dates |
| 4 | `report_generator.py` | Produces a styled HTML report with XIRR as the headline figure, portfolio returns table, account breakdown, and benchmark comparison cards |
| 5 | `run.py` | Writes a plain-text snapshot to `snapshots/` and appends one row to `snapshots/hm_history.csv` |

---

## Outputs in detail

### HTML report (`reports/`)
Opens automatically in your browser after each run. Shows:
- XIRR (annualised return) — headline figure
- Net invested, current value, P&L, total return, avg time held
- Per-account balance breakdown
- Benchmark comparison cards (P&L, final value, total return, IRR for each ETF)

### Plain-text snapshot (`snapshots/hm_snapshot_YYYYMMDD_HHMMSS.txt`)
Human-readable summary in a fixed layout

### History CSV (`snapshots/hm_history.csv`)
One row appended per run. Columns: date, net_investment, pnl, final_value, total_return_pct, irr_pct, avg_time_held_days, net_time_weighted_investment, then per-ETF columns (e.g. `VWRP_L_irr_pct`), then per-account balances and total. Useful for charting performance over time in Excel.

---

## Benchmark ETFs

The default tickers and what they represent:

| Ticker | Description |
|--------|-------------|
| `VWRP.L` | Vanguard FTSE All-World — global equities (accumulating) |
| `XDER.L` | Xtrackers FTSE EPRA/NAREIT — European property (accumulating) |
| `SLXX.L` | iShares GBP Corp Bond — corporate bonds (distributing) |

To change benchmarks, update `ETF_TICKERS` in your `.env` file. Any Yahoo Finance ticker works.

Note: SLXX is distributing rather than accumulating, which makes the comparison slightly imperfect (distributions are not reinvested in the model).

---

## Troubleshooting

**Scraper times out on login or navigation**
Run with `--visible` to watch the browser. The site is an Angular SPA — occasional slowness is normal. If it consistently fails, the site may have been updated; check `hm_staging/*_debug.png` for a screenshot of the failure point.

**Balance reads the wrong number**
The scraper takes the largest £ value from the dashboard summary cards. If the site layout changes, inspect the dashboard HTML and update the `card_sel` selector in `housemartin_scraper.py`.

**"All accounts" filter doesn't apply**
The account filter is a Select2 jQuery component. The scraper opens it, picks a specific account first, applies, then picks All accounts and applies again — the toggle forces Angular's change detection. If it breaks, run `--visible` and watch step 5a/5b in the logs.

**CSV download times out**
The site generates CSV downloads as a browser Blob (client-side). The scraper intercepts this via a JS monkey-patch on `document.createElement`. If the blob interceptor returns nothing, it falls back to Playwright's standard download handler. If both fail, check `hm_staging/*_export_fallback_debug.png`.

**XIRR calculation fails**
Most commonly caused by the current value not being correctly placed in the `Cash change` column of the merged CSV. Check `ReportsTransactionAll.csv` — the first row (Current Value) should have a number in column I, not column J.

---

## Scheduling (optional)

To run automatically on a schedule on Windows, use Task Scheduler:

1. Open Task Scheduler → Create Basic Task
2. Trigger: Monthly (or weekly — pick your preferred cadence)
3. Action: Start a program
   - Program: `python`
   - Arguments: `run.py`
   - Start in: `C:\path\to\housemartin\`

For a more robust setup, create a `.bat` file:
```bat
@echo off
cd /d C:\path\to\housemartin
python run.py >> logs\run.log 2>&1
```
And point Task Scheduler at the `.bat` file instead.
