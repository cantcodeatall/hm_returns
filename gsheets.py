"""
gsheets.py  ·  Push scraper results to Google Sheets
=====================================================
Writes a snapshot row to the "HM" sheet after each run.

Sheet layout (written by this script, created automatically if missing):
  Row 1: headers (written once)
  Row 2+: one row per run, newest at top (rows are inserted, not appended)

Configure in .env:
    GSHEET_ID=insert_your_google_sheet_id_here
    GSHEET_SHEET_NAME=HM          (default: HM)
    GSHEET_KEY_FILE=service_account.json

Install once:
    pip install google-auth google-auth-httplib2 google-api-python-client
"""

import os
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

SCOPES      = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_ID    = os.getenv("GSHEET_ID", "")
SHEET_NAME  = os.getenv("GSHEET_SHEET_NAME", "HM")
KEY_FILE    = Path(__file__).parent / os.getenv("GSHEET_KEY_FILE", "service_account.json")


def _get_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not KEY_FILE.exists():
        raise FileNotFoundError(
            f"Service account key not found: {KEY_FILE}\n"
            "Put service_account.json in the same folder as run.py"
        )
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_FILE), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_sheet_id(svc, title: str) -> int:
    """Return the numeric sheetId for the named tab."""
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == title:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"Sheet tab '{title}' not found in spreadsheet")


def push_results(
    *,
    snapshot_date: str,
    # Portfolio-level
    net_investment: float,
    current_value:  float,
    pnl:            float,
    total_return:   float,
    irr:            float,
    # Per account holder (list of dicts)
    account_results: list,      # [{name, balance}]
    # Per sub-account (list of dicts)
    sub_account_results: list,  # [{holder, label, current_value, gross_investment, cash, pnl, total_return, irr}]
    # Benchmark ETFs
    benchmarks: list,           # [{ticker, irr, pnl, final_value, total_return}]
    ticker_names: dict = None,  # {ticker: display_name}
):
    """
    Push one snapshot to the HM sheet.
    Inserts a new row at row 2 (below header) so newest is always on top.
    Creates headers on first run.
    """
    if not SHEET_ID:
        raise ValueError("GSHEET_ID not set in .env")

    svc      = _get_service()
    sheets   = svc.spreadsheets()
    tab      = SHEET_NAME
    ticker_names = ticker_names or {}

    # ── Build the flat row ────────────────────────────────────────────────────
    def pct(v):  return round(v * 100, 4) if v is not None else ""
    def gbp(v):  return round(v, 2)       if v is not None else ""

    headers = ["Date", "Current Value", "Net Invested", "P&L",
               "Total Return %", "XIRR %"]

    values  = [snapshot_date,
               gbp(current_value), gbp(net_investment), gbp(pnl),
               pct(total_return), pct(irr)]

    # Per account holder totals
    for r in account_results:
        headers.append(f"{r['name']} Balance")
        values.append(gbp(r["balance"]))

    # Per sub-account: invested, cash, total, P&L, return, XIRR
    for sa in sub_account_results:
        prefix = f"{sa['holder']} {sa['label']}"
        headers += [f"{prefix} Invested", f"{prefix} Cash",
                    f"{prefix} Total", f"{prefix} P&L",
                    f"{prefix} Return %", f"{prefix} XIRR %"]
        values  += [gbp(sa.get("gross_investment")), gbp(sa.get("cash")),
                    gbp(sa.get("current_value")),    gbp(sa.get("pnl")),
                    pct(sa.get("total_return")),     pct(sa.get("irr"))]

    # Benchmark ETFs
    for b in benchmarks:
        name = ticker_names.get(b["ticker"], b["ticker"])
        headers += [f"{name} Final Value", f"{name} P&L",
                    f"{name} Return %",    f"{name} XIRR %"]
        values  += [gbp(b.get("final_value")), gbp(b.get("pnl")),
                    pct(b.get("total_return")), pct(b.get("irr"))]

    MAX_HISTORY = 50  # max data rows to keep (excluding header)

    # ── Check if header row exists ────────────────────────────────────────────
    existing = sheets.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!1:1"
    ).execute().get("values", [])

    numeric_sheet_id = _get_sheet_id(svc, tab)

    if not existing:
        # Write header row first
        sheets.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [headers]}
        ).execute()
        print(f"  → Created header row in '{tab}' ({len(headers)} columns)")

    # ── Count existing data rows (rows 2 onwards) ─────────────────────────────
    all_rows = sheets.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!A:A"
    ).execute().get("values", [])
    data_row_count = max(0, len(all_rows) - 1)  # subtract header

    requests = []

    # ── Insert blank row at position 2 (below header) ────────────────────────
    requests.append({
        "insertDimension": {
            "range": {
                "sheetId":    numeric_sheet_id,
                "dimension":  "ROWS",
                "startIndex": 1,   # 0-indexed → row 2
                "endIndex":   2,
            },
            "inheritFromBefore": False,
        }
    })

    # ── If at cap, delete the last (oldest) data row ──────────────────────────
    if data_row_count >= MAX_HISTORY:
        delete_row_index = data_row_count + 1  # +1 because we just inserted one
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId":    numeric_sheet_id,
                    "dimension":  "ROWS",
                    "startIndex": delete_row_index,   # 0-indexed
                    "endIndex":   delete_row_index + 1,
                }
            }
        })
        print(f"  → History cap reached ({MAX_HISTORY}): oldest row deleted")

    sheets.batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": requests}
    ).execute()

    # ── Write data into the newly inserted row 2 ──────────────────────────────
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range=f"{tab}!A2",
        valueInputOption="USER_ENTERED",
        body={"values": [values]}
    ).execute()

    print(f"  → Pushed {len(values)} columns to '{tab}' (history: {min(data_row_count + 1, MAX_HISTORY)}/{MAX_HISTORY}) ✓")
