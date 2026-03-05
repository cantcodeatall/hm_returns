"""
run.py  ·  Housemartin Returns Automation
==========================================
Orchestrates the full pipeline:
  1. Playwright: log in to all accounts, grab balances, download CSVs
  2. merge_csv.py: merge the CSVs + inject total balance
  3. irr5.py: calculate XIRR / IRR
  4. report_generator.py: write a polished HTML report

Usage:
    python run.py                  # headless (no browser window)
    python run.py --visible        # show browser (great for debugging selectors)
    python run.py --skip-scrape    # skip login/download, reuse files in hm_staging/
"""

import sys
import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# ── Bootstrap: make sure we can import sibling modules ────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from housemartin_scraper import run_scraper, STAGING_DIR
from merge_csv import merge_csv_files
from irr5 import calculate_irr, calculate_etf_irr, create_additional_dataframe
from report_generator import generate_report
import numpy as np


# ── Config ────────────────────────────────────────────────────────────────────
# Benchmark ETFs — edit freely or override via ETF_TICKERS env var (comma-separated)
DEFAULT_ETF_TICKERS = "VWRP.L,XDER.L,SLXX.L"
ETF_TICKERS = [
    t.strip()
    for t in os.getenv("ETF_TICKERS", DEFAULT_ETF_TICKERS).split(",")
    if t.strip()
]

MERGED_CSV      = HERE / "ReportsTransactionAll.csv"
REPORTS_DIR     = HERE / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
SNAPSHOTS_DIR   = HERE / "snapshots"
SNAPSHOTS_DIR.mkdir(exist_ok=True)


def stamp():
    return datetime.now().strftime("%H:%M:%S")


def step(n, desc):
    print(f"\n[{stamp()}] ── Step {n}: {desc}")


# ── Pipeline ──────────────────────────────────────────────────────────────────

def main():
    skip_scrape = "--skip-scrape" in sys.argv
    headless    = "--visible"     not in sys.argv

    print("=" * 55)
    print("  Housemartin Returns Automation")
    print(f"  {datetime.now().strftime('%A, %d %B %Y  %H:%M')}")
    print("=" * 55)

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    step(1, "Logging in & scraping accounts")

    if skip_scrape:
        print("  ⚡ --skip-scrape: reusing existing files in hm_staging/")
        # Re-derive balances from env (or prompt user)
        account_results = _load_staging_results()
    else:
        account_results = run_scraper(headless=headless)

    if not account_results:
        print("  ✗  No accounts scraped. Check credentials in .env")
        sys.exit(1)

    total_balance = sum(r["balance"] for r in account_results)
    print(f"\n  Total consolidated balance: £{total_balance:,.2f}")
    for r in account_results:
        print(f"    {r['name']}: £{r['balance']:,.2f}")

    # ── Step 2: Merge CSVs ────────────────────────────────────────────────────
    step(2, "Merging transaction CSVs")

    csv_paths = [r["csv_path"] for r in account_results]
    # Copy all CSVs into a temp merge folder
    merge_input_dir = HERE / "hm_merge_input"
    if merge_input_dir.exists():
        shutil.rmtree(merge_input_dir)
    merge_input_dir.mkdir()

    for p in csv_paths:
        shutil.copy(p, merge_input_dir / p.name)

    merge_csv_files(
        input_path=str(merge_input_dir),
        output_file=str(MERGED_CSV),
        current_value=total_balance,
    )

    # ── Step 3: Calculate IRR + all benchmark ETFs ───────────────────────────
    step(3, f"Calculating XIRR + {len(ETF_TICKERS)} benchmarks: {', '.join(ETF_TICKERS)}")

    # Portfolio IRR is calculated once (using first ETF for the base call)
    irr_result, net_investment, time_difference, df, _ = calculate_irr(
        str(MERGED_CSV), ETF_TICKERS[0]
    )

    description_col = "Description" if "Description" in df.columns else df.columns[2]
    current_value   = df.loc[df[description_col] == "Current Value", "Cash Flow"].values[0]

    df2 = create_additional_dataframe(df)

    pnl          = current_value - net_investment
    total_return = pnl / net_investment if net_investment != 0 else 0

    # Calculate metrics for each benchmark ETF
    benchmarks = []
    for ticker in ETF_TICKERS:
        print(f"  → Processing benchmark: {ticker}")
        try:
            _, _, _, df_b, etf_df = calculate_irr(str(MERGED_CSV), ticker)
            etf_irr_result    = calculate_etf_irr(df_b, etf_df)
            etf_final_value   = etf_df["ETF Value"].iloc[-1]
            etf_net_cash_flow = -df_b.loc[df_b[description_col].isin(["Deposit", "Withdraw"]), "Cash Flow"].sum()
            etf_pnl           = etf_final_value - etf_net_cash_flow
            etf_total_return  = etf_pnl / etf_net_cash_flow if etf_net_cash_flow != 0 else 0
            benchmarks.append({
                "ticker":       ticker,
                "irr":          etf_irr_result,
                "pnl":          etf_pnl,
                "final_value":  etf_final_value,
                "total_return": etf_total_return,
            })
            irr_str = f"{etf_irr_result * 100:.2f}%" if etf_irr_result else "N/A"
            print(f"     XIRR: {irr_str}  |  P&L: £{etf_pnl:,.2f}")
        except Exception as e:
            print(f"  ⚠  Could not calculate benchmark for {ticker}: {e}")
            benchmarks.append({"ticker": ticker, "irr": None, "pnl": None,
                                "final_value": None, "total_return": None})

    # Print console summary
    print(f"\n  Net Investment : £{net_investment:,.2f}")
    print(f"  P&L            : £{pnl:,.2f}")
    print(f"  Final Value    : £{current_value:,.2f}")
    print(f"  Total Return   : {total_return * 100:.2f}%")
    print(f"  XIRR           : {irr_result * 100:.2f}%" if irr_result else "  XIRR: N/A")

    # ── Step 4: Generate HTML report ──────────────────────────────────────────
    step(4, "Generating HTML report")

    report_filename = REPORTS_DIR / f"hm_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    latest_report   = REPORTS_DIR / "hm_report_latest.html"

    generate_report(
        irr_result       = irr_result,
        net_investment   = net_investment,
        time_difference  = time_difference,
        current_value    = current_value,
        pnl              = pnl,
        total_return     = total_return,
        benchmarks       = benchmarks,
        account_balances = account_results,
        output_path      = str(report_filename),
    )

    # Keep a "latest" copy for easy access
    shutil.copy(report_filename, latest_report)

    # ── Step 5: Write snapshot txt + append to CSV history ───────────────────
    step(5, "Writing snapshot & history")

    now_dt   = datetime.now()
    date_str = now_dt.strftime("%Y/%m/%d %H:%M")
    ts_str   = now_dt.strftime("%Y%m%d_%H%M%S")

    def fmt_pct(v):
        return f"{v * 100:.2f}%" if v is not None else "N/A"
    def fmt_gbp(v):
        return f"{v:.2f}" if v is not None else "N/A"

    # ── Ticker display names — built from ETF_LABELS env var ────────────────
    # Set ETF_LABELS in .env as: VWRP.L=Global Equities (VWRP),XDER.L=European Property (XDER)
    # Any ticker without a label entry just shows the raw ticker string.
    TICKER_NAMES = {}
    for entry in os.getenv("ETF_LABELS", "").split(","):
        entry = entry.strip()
        if "=" in entry:
            k, v = entry.split("=", 1)
            TICKER_NAMES[k.strip()] = v.strip()
    for t in ETF_TICKERS:
        TICKER_NAMES.setdefault(t, t)

    # ── Build txt content ─────────────────────────────────────────────────────
    lines = []
    lines.append("===========")
    lines.append(date_str)
    lines.append("===========")
    lines.append(f"Net Investment: {fmt_gbp(net_investment)}")
    lines.append(f"P&L: {fmt_gbp(pnl)}")
    lines.append(f"Final Value: {fmt_gbp(current_value)}")
    lines.append(f"Total return: {fmt_pct(total_return)}")
    lines.append(f"Internal rate of return (IRR): {fmt_pct(irr_result)}")
    lines.append("===========")
    td = time_difference
    if td:
        y = td.days // 365
        m = (td.days % 365) // 30
        d = (td.days % 365) % 30
        lines.append(f"Value-Weighted Average Time Held: {y} years, {m} months, {d} days")
    df2 = create_additional_dataframe(df)
    net_twi = -df2["Product"].sum()
    lines.append(f"Net time weighted investment (equivalent investment held for 1 year): {net_twi:.2f}")
    lines.append("===========")
    for b in benchmarks:
        name = TICKER_NAMES.get(b["ticker"], b["ticker"])
        lines.append(f"{name} equivalent investment returns:")
        lines.append(f"P&L: {fmt_gbp(b['pnl'])}")
        lines.append(f"Final value: {fmt_gbp(b['final_value'])}")
        lines.append(f"Total Return: {fmt_pct(b['total_return'])}")
        lines.append(f"Internal rate of return (IRR): {fmt_pct(b['irr'])}")
        lines.append("  ")
    lines.append("===========")
    # Per-account balances
    for a in account_results:
        lines.append(f"  {a['name']}: £{a['balance']:,.2f}")
    lines.append(f"  TOTAL: £{sum(a['balance'] for a in account_results):,.2f}")

    txt_content = "\n".join(lines)

    # Save timestamped txt snapshot
    txt_path = SNAPSHOTS_DIR / f"hm_snapshot_{ts_str}.txt"
    txt_path.write_text(txt_content, encoding="utf-8")
    print(f"  → Snapshot txt : {txt_path}")

    # ── Append to CSV history ─────────────────────────────────────────────────
    import csv
    csv_path = SNAPSHOTS_DIR / "hm_history.csv"
    
    # Build header + row
    base_fields = ["date", "net_investment", "pnl", "final_value",
                   "total_return_pct", "irr_pct",
                   "avg_time_held_days", "net_time_weighted_investment"]
    bench_fields = []
    for b in benchmarks:
        t = b["ticker"].replace(".", "_")
        bench_fields += [f"{t}_pnl", f"{t}_final_value",
                         f"{t}_total_return_pct", f"{t}_irr_pct"]
    acct_fields  = [a["name"].replace(" ", "_") for a in account_results]
    acct_fields += ["total_balance"]
    all_fields   = base_fields + bench_fields + acct_fields

    row = {
        "date":                        date_str,
        "net_investment":              round(net_investment, 2),
        "pnl":                         round(pnl, 2),
        "final_value":                 round(current_value, 2),
        "total_return_pct":            round(total_return * 100, 4) if total_return else None,
        "irr_pct":                     round(irr_result * 100, 4)   if irr_result   else None,
        "avg_time_held_days":          td.days if td else None,
        "net_time_weighted_investment": round(net_twi, 2),
    }
    for b in benchmarks:
        t = b["ticker"].replace(".", "_")
        row[f"{t}_pnl"]              = round(b["pnl"], 2)          if b["pnl"]          is not None else None
        row[f"{t}_final_value"]      = round(b["final_value"], 2)  if b["final_value"]  is not None else None
        row[f"{t}_total_return_pct"] = round(b["total_return"] * 100, 4) if b["total_return"] is not None else None
        row[f"{t}_irr_pct"]          = round(b["irr"] * 100, 4)   if b["irr"]          is not None else None
    for a in account_results:
        row[a["name"].replace(" ", "_")] = round(a["balance"], 2)
    row["total_balance"] = round(sum(a["balance"] for a in account_results), 2)

    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    print(f"  → History CSV  : {csv_path}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 55}")
    print("  ✓  All done!")
    print(f"  Report   : {report_filename}")
    print(f"  Snapshot : {txt_path}")
    print(f"  History  : {csv_path}")
    print(f"{'=' * 55}\n")

    # Open report in default browser on Windows
    if sys.platform == "win32":
        os.startfile(str(latest_report))


def _load_staging_results():
    """
    When --skip-scrape is used: find CSVs already in hm_staging/ and ask
    the user to enter balances manually (or read from a sidecar .txt).
    """
    staging = STAGING_DIR
    csvs    = list(staging.glob("*.csv"))
    if not csvs:
        print(f"  ✗  No CSVs found in {staging}. Run without --skip-scrape first.")
        sys.exit(1)

    results = []
    print(f"  Found {len(csvs)} CSV(s) in staging. Enter balances manually:")
    for csv in sorted(csvs):
        name = csv.stem.replace("_transactions", "").replace("_", " ")
        while True:
            try:
                val = float(input(f"    Balance for {name} (£): ").replace(",", "").replace("£", ""))
                results.append({"name": name, "balance": val, "csv_path": csv})
                break
            except ValueError:
                print("    Invalid amount, try again.")
    return results


if __name__ == "__main__":
    main()
