# Housemartin Returns Automation

Automates the full pipeline for a Housemartin portfolio: logs into all accounts, scrapes balances and sub-account breakdowns, downloads transaction CSVs, merges them, calculates XIRR, produces an HTML report, writes local snapshots, pushes results to Google Sheets, and updates Portfolio Performance data.

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
├── gsheets.py              ← Google Sheets push module
├── pp_index.py             ← Portfolio Performance NAV + transaction builder
├── process_history.py      ← batch-processes historical CSV files
├── snapshots5.txt          ← historical manual snapshots (required for PP seed)
├── service_account.json    ← Google service account key (never commit this)
├── .env                    ← your credentials (never commit this)
└── .env.example            ← template — copy to .env and fill in
```

Generated at runtime:

```
├── ReportsTransactionAll.csv            ← merged transaction history
├── hmfund_quotes.csv                    ← HMFUND daily NAV (PP import)
├── hmfund_transactions_seed.csv         ← PP transactions (PP import)
├── hm_pp_state.json                     ← PP incremental state
├── hm_pp_seed_done.flag                 ← prevents re-running PP seed
├── hm_staging/                          ← per-account CSVs + debug screenshots
├── reports/
│   ├── hm_report_YYYYMMDD_HHMMSS.html  ← timestamped HTML report
│   └── hm_report_latest.html           ← always the most recent (auto-opens)
└── snapshots/
    ├── hm_snapshot_YYYYMMDD_HHMMSS.txt ← plain-text performance snapshot
    └── hm_history.csv                  ← one row per run, grows over time
```

---

## Setup (one-time)

### 1. Install Python dependencies

```bash
pip install playwright pandas numpy scipy yfinance python-dotenv google-auth google-auth-httplib2 google-api-python-client
playwright install chromium
```

### 2. Configure credentials

```bash
copy .env.example .env       # Windows
# cp .env.example .env       # Mac/Linux
```

Open `.env` and fill in your details:

```ini
HM_ACCOUNT1_NAME=Account1
HM_ACCOUNT1_EMAIL=account1@example.com
HM_ACCOUNT1_PASSWORD=yourpassword

HM_ACCOUNT2_NAME=Account2
HM_ACCOUNT2_EMAIL=account2@example.com
HM_ACCOUNT2_PASSWORD=yourpassword

HM_ACCOUNT3_NAME=Account3
HM_ACCOUNT3_EMAIL=account3@example.com
HM_ACCOUNT3_PASSWORD=yourpassword

# Benchmark ETFs — comma-separated Yahoo Finance tickers
ETF_TICKERS=VWRP.L,XDER.L,VAGS.L

# Display labels for each ticker — must match ETF_TICKERS exactly
ETF_LABELS=VWRP.L=Global Equities (VWRP),XDER.L=European Property (XDER),VAGS.L=Global Bonds (VAGS)

# Google Sheets (see Google Sheets section below)
GSHEET_ID=your_google_sheet_id_here
GSHEET_SHEET_NAME=HM
GSHEET_KEY_FILE=service_account.json
```

Accounts with blank credentials are automatically skipped — if you only have two accounts, leave the third block empty.

Passwords containing special characters (e.g. `$`) should be wrapped in single quotes:
```ini
HM_ACCOUNT1_PASSWORD='p@ssw0rd$example'
```

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

### Skip scraping (reuse CSVs already in `hm_staging/`, enter balances manually):
```bash
python run.py --skip-scrape
```

---

## What each step does

| Step | What happens |
|------|--------------|
| 1 | Logs into each account, clicks each dashboard tab (Regular/ISA) to read per-sub-account balances (invested, cash, total — including withdrawals awaiting authorisation), then downloads a separate CSV for each sub-account and one for All accounts |
| 2 | Merges the All-accounts CSVs, injects the total current balance as a "Current Value" row in the Cash change column (required for XIRR) |
| 3 | Calculates XIRR for the consolidated portfolio, and separately for each sub-account using its scraped balance as current value |
| 3b | Calculates XIRR for each benchmark ETF using the same cash flow dates |
| 4 | Produces a styled HTML report: XIRR headline, portfolio returns (including current HMFUND NAV), sub-account breakdown, and benchmark comparison cards |
| 5 | Writes a plain-text snapshot to `snapshots/` and appends one row to `snapshots/hm_history.csv` (includes `hmfund_nav` column) |
| 6 | Pushes results to Google Sheets HM tab (rolling 50-run history) |
| 7 | Updates HMFUND NAV and transactions in Google Sheets for Portfolio Performance import |

---

## Outputs in detail

### HTML report (`reports/`)
Opens automatically in your browser after each run. Shows:
- XIRR (annualised return) — headline figure
- Net invested, current value, P&L, total return, avg time held, HMFUND NAV
- Sub-account breakdown: invested, cash, total value, P&L, total return, XIRR per account holder and account type (Regular/ISA)
- Benchmark comparison cards (P&L, final value, total return, XIRR for each ETF)

### Plain-text snapshot (`snapshots/hm_snapshot_YYYYMMDD_HHMMSS.txt`)
Human-readable summary saved after each run, including portfolio-level figures, sub-account breakdown, and benchmark comparisons.

### History CSV (`snapshots/hm_history.csv`)
One row appended per run. Columns: date, net_investment, pnl, final_value, total_return_pct, irr_pct, avg_time_held_days, net_time_weighted_investment, hmfund_nav, per-ETF columns (e.g. `VWRP_L_irr_pct`), per-account balances, per-sub-account XIRR and values, and total. Useful for charting performance over time in Excel.

---

## Benchmark ETFs

The default tickers and what they represent:

| Ticker | Description |
|--------|-------------|
| `VWRP.L` | Vanguard FTSE All-World — global equities (accumulating) |
| `XDER.L` | Xtrackers FTSE EPRA/NAREIT — European property (accumulating) |
| `VAGS.L` | Vanguard Global Aggregate Bond — global bonds (accumulating) |

To change benchmarks, update both `ETF_TICKERS` and `ETF_LABELS` in your `.env` file — no Python files need changing. Any Yahoo Finance ticker works.

---

## Google Sheets integration

After every run, results are automatically pushed to a Google Sheet tab. The sheet keeps a rolling history of the last 50 runs — newest at the top, oldest automatically deleted once the cap is reached.

### Sheet layout

Headers are created automatically on the first run. Columns are:

| Date | Current Value | Net Invested | P&L | Total Return % | XIRR % | Account1 Balance | … | Account1 Regular Invested | Account1 Regular Cash | … | ETF1 Final Value | … |

Each run inserts a new row at position 2 (below the header), pushing older rows down. Once 50 data rows exist, the oldest is deleted in the same operation.

### Setup (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → download the JSON key file
4. Rename it `service_account.json` and place it in the same folder as `run.py`
5. In your Google Sheet → Share → add the service account email (looks like `name@project.iam.gserviceaccount.com`) with **Editor** access
6. Create a tab named `HM` in your spreadsheet (or set a different name via `GSHEET_SHEET_NAME` in `.env`)
7. Add the three `GSHEET_*` lines to your `.env` (see above)

The Sheet ID is the long string in your sheet URL:
`https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`

The push is non-fatal — if it fails for any reason the rest of the run completes normally and the error is shown as a warning.

---

## Portfolio Performance integration

`pp_index.py` builds a synthetic fund (`HMFUND`) for import into [Portfolio Performance](https://www.portfolio-performance.info/). One security represents the entire platform. Per-account accuracy is achieved through buy/sell transactions at prices that deviate from NAV to correctly reflect each account's historical performance.

### NAV formula

The NAV is chain-linked, stripping out external cash flows between each point:

```
CF_t     = NI_t − NI_{t−1}                  # net deposit or withdrawal
units_t  = units_{t−1} + CF_t / NAV_{t−1}   # adjust unit count for flows
NAV_t    = Value_t / units_t                 # pure performance, no flow distortion
```

Starting point: **2023-01-01, NAV = 100.00**, units = NI_first / 100 = 7199.61.
First snapshot (2023-08-30) NAV ≈ 105.48, reflecting 8 months of performance.
NAV between sparse snapshot dates is linearly interpolated.
Income (interest, underwriter fees) is captured as NAV appreciation automatically — no separate transaction needed, like an accumulating fund.

### Transaction approach

**Pre-split period** (before the first automated snapshot with per-account data):

For each account, the script solves for the annual rate `r` that makes the future value of all historical cash flows equal the known account balance on the split date. It then forward-simulates the implied balance at every cash flow date using that rate. At each flow date:

1. A Buy or Sell at NAV for the cash flow amount
2. A rebalance pair (Buy `units_new` at NAV + Sell `units_old` at `implied_balance / units_old`, net cash = 0) to set holdings to the correct implied balance

This means PP's IRR for each account matches the XIRR calculated by the main pipeline from inception.

**Post-split period** (daily, from first automated snapshot onwards):

Each day, for each account:
1. If net invested changed → external flow Buy/Sell at NAV (the actual deposit/withdrawal)
2. Performance rebalance on the residual unit delta (net cash = 0): Buy `units_new` at NAV + Sell `units_old` at `val_perf / units_old`, where `val_perf` strips out the flow — so P&L is not distorted by deposits or withdrawals

### Transaction format

Transactions are semicolon-delimited with columns:
`Date;Type;Value;Shares;Quote;ISIN;Ticker;Securities Account`

- `Type` is always `Buy` or `Sell` — no Deposit/Removal rows
- Import as **Portfolio Transactions** in PP, tick **Convert to Delivery (Inbound/Outbound)** to avoid cash balance entries
- `ISIN`: `XX000HM00001`, `Ticker`: `HM` (same for all rows)
- Securities Account names: one per sub-account, configured in `pp_index.py` under the `ACCOUNTS` dict (e.g. `Account1 Reg`, `Account1 ISA`)

### Google Sheets tabs produced

| Tab | Contents |
|-----|----------|
| `HM_pp_quotes` | `Date;Close` — daily NAV for HMFUND (semicolon-delimited) |
| `HM_pp_transactions` | All buy/sell transactions, full replace on every run (semicolon-delimited) |

### Setup (one-time)

**1.** Place `snapshots5.txt` in the working folder (same folder as `run.py`).

**2.** Run the historical seed:
```bash
python pp_index.py
```
This produces `hmfund_quotes.csv`, `hmfund_transactions_seed.csv`, `hm_pp_state.json` locally and pushes both to Google Sheets. The log shows the solved rate per account and confirms the simulated balance matches the target.

**3.** In Google Sheets → File → Share → Publish to web → publish `HM_pp_quotes` and `HM_pp_transactions` as CSV. Copy the two URLs.

**4.** In Portfolio Performance:
- Create security `HMFUND` (ISIN `XX000HM00001`, ticker `HM`), starting price 100.00 on 2023-01-01
- Historical prices → Add → From URL → paste `HM_pp_quotes` URL (semicolon separator, Date / Close columns)
- Create one securities account per sub-account, using the names defined in the `ACCOUNTS` dict in `pp_index.py`
- Import `HM_pp_transactions` as Portfolio Transactions (semicolon separator), tick Convert to Delivery

**Going forward**, Step 7 of the daily run calls `daily_update()` automatically, appending the new NAV quote and any transactions, then rewrites both Google Sheets tabs with the full updated data.

### Re-running the seed

If you need to regenerate all historical transactions (e.g. after correcting data):
```bash
python pp_index.py --reseed
```
This clears the seed-done flag and rebuilds everything from scratch, then rewrites both Google Sheets tabs completely. Re-import the transactions into PP afterwards (PP deduplicates on Date+Type+Shares+Quote, so existing entries won't be doubled).

### State file

`hm_pp_state.json` stores the last known NAV, total units, net invested, and per-account unit holdings. Updated after every run. If deleted or corrupted, re-run `python pp_index.py` to regenerate.

---

## Processing historical data

If you have existing merged CSV files from before this automation (named `ReportsTransactionAllYYYYMMDD.csv`), you can back-calculate XIRR for all of them in one go:

```bash
python process_history.py                          # CSVs in current folder
python process_history.py path\to\history\folder   # specify folder
```

Produces `hm_historical_xirr.csv` with one row per file — same column structure as the live history CSV.

---

## Troubleshooting

**Scraper times out on login or navigation**
Run with `--visible` to watch the browser. If it consistently fails, the site may have been updated; check `hm_staging/*_debug.png` for a screenshot of the failure point.

**Wrong balance detected**
The scraper looks for the card labelled "Total platform balance" on each dashboard tab. If the site layout changes, check `hm_staging/*_balance_debug.png` and update the card-reading logic in `housemartin_scraper.py`.

**Sub-account tabs not found**
The scraper discovers tabs via JavaScript by finding short elements containing the word "account". Check the log line `Dashboard account elements found: [...]` — if the expected tabs aren't listed, the site HTML has changed.

**"All accounts" filter doesn't apply**
The account filter is an Angular `ng-select` component. The scraper opens it, picks a specific account first, applies, then picks All accounts and applies again — the toggle forces Angular's change detection. Run with `--visible` and watch the step 5a/5b log lines.

**CSV download fails**
The site generates CSV downloads as a browser Blob (client-side). The scraper intercepts this via a JS hook on `document.createElement` and `URL.createObjectURL`. It retries up to 3 times automatically. If all attempts fail, check `hm_staging/*_export_fallback_debug.png`.

**XIRR calculation fails**
Most commonly caused by the current value not being placed in the `Cash change` column of the merged CSV. Open `ReportsTransactionAll.csv` — the first row (Current Value) should have a number in column I, not column J.

**Google Sheets push fails**
Check that `service_account.json` is in the same folder as `run.py`, that the sheet has been shared with the service account email with Editor access, and that `GSHEET_ID` and `GSHEET_SHEET_NAME` in `.env` match your actual spreadsheet. The error is non-fatal so the rest of the run will complete.

**PP seed produces wrong balances**
Re-run with `python pp_index.py --reseed`. Check the log output — each account should show `error=£0.00` (or very close). If an account's error is large, check that the XIRR columns in `hm_history.csv` are populated for that account on the split date (2026-03-14).

---

## Scheduling (optional)

To run automatically on Windows, use Task Scheduler:

1. Open Task Scheduler → Create Basic Task
2. Trigger: monthly (or weekly)
3. Action: Start a program
   - Program: `python`
   - Arguments: `run.py`
   - Start in: `C:\path\to\housemartin\`

For a more robust setup, use a `.bat` file:
```bat
@echo off
cd /d C:\path\to\housemartin
python run.py >> logs\run.log 2>&1
```
And point Task Scheduler at the `.bat` file instead.
