"""
report_generator.py
Generates a polished HTML returns report from IRR calculation results.
Supports multiple benchmark ETFs displayed side-by-side.
"""

from datetime import datetime
from pathlib import Path


import os

def build_ticker_labels(tickers=None):
    """
    Build display-name map from ETF_LABELS env var.
    Format: VWRP.L=Global Equities (VWRP),XDER.L=European Property (XDER)
    Falls back to the raw ticker string for anything not listed.
    """
    labels = {}
    for entry in os.getenv("ETF_LABELS", "").split(","):
        entry = entry.strip()
        if "=" in entry:
            k, v = entry.split("=", 1)
            labels[k.strip()] = v.strip()
    if tickers:
        for t in tickers:
            labels.setdefault(t, t)
    return labels


def generate_report(
    irr_result,
    net_investment,
    time_difference,
    current_value,
    pnl,
    total_return,
    benchmarks: list,
    account_balances: list,
    sub_accounts: list = None,
    hmfund_nav       = None,
    output_path: str = "housemartin_report.html",
):
    total_balance = sum(a["balance"] for a in account_balances)
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    TICKER_LABELS = build_ticker_labels([b["ticker"] for b in benchmarks])

    def fmt_pct(v):
        if v is None:
            return "N/A"
        return f"{v * 100:.2f}%"

    def fmt_gbp(v):
        if v is None:
            return "N/A"
        return f"£{v:,.2f}"

    def fmt_time(td):
        if td is None:
            return "N/A"
        y = td.days // 365
        m = (td.days % 365) // 30
        d = (td.days % 365) % 30
        parts = []
        if y: parts.append(f"{y}y")
        if m: parts.append(f"{m}m")
        if d: parts.append(f"{d}d")
        return " ".join(parts) if parts else "< 1 day"

    def sign_class(v):
        if v is None:
            return ""
        return "pos" if v >= 0 else "neg"

    def outperform(b_irr):
        if irr_result is None or b_irr is None:
            return None
        return irr_result - b_irr

    # ── Account rows ──────────────────────────────────────────────────────────
    account_rows = "\n".join(
        f'<tr><td>{a["name"]}</td><td class="num">£{a["balance"]:,.2f}</td></tr>'
        for a in account_balances
    )

    # ── Sub-account rows ───────────────────────────────────────────────────────
    def fmt_irr(v):
        return f"{v*100:.2f}%" if v is not None else "N/A"

    sub_account_rows = ""
    if sub_accounts:
        for sa in sub_accounts:
            irr_val   = fmt_irr(sa.get("irr"))
            cv_val    = fmt_gbp(sa.get("current_value"))
            pnl_val   = fmt_gbp(sa.get("pnl"))
            ret_val   = fmt_pct(sa.get("total_return"))
            inv_val   = fmt_gbp(sa.get("gross_investment"))
            cash_val  = fmt_gbp(sa.get("cash"))
            sc        = sign_class(sa.get("pnl"))
            sub_account_rows += f"""
      <tr>
        <td>{sa["holder"]}</td>
        <td>{sa["label"]}</td>
        <td class="num">{inv_val}</td>
        <td class="num">{cash_val}</td>
        <td class="num">{cv_val}</td>
        <td class="num {sc}">{pnl_val}</td>
        <td class="num {sc}">{ret_val}</td>
        <td class="num gold"><strong>{irr_val}</strong></td>
      </tr>"""

    irr_pct = f"{irr_result * 100:.2f}%" if irr_result is not None else "N/A"

    # ── Benchmark cards ───────────────────────────────────────────────────────
    def benchmark_card(b):
        ticker   = b["ticker"]
        label    = TICKER_LABELS.get(ticker, ticker)
        b_irr    = b["irr"]
        op       = outperform(b_irr)
        op_class = sign_class(op)
        op_str   = f"{op * 100:+.2f}%" if op is not None else "N/A"
        b_irr_str = fmt_pct(b_irr)

        return f"""
        <div class="bench-card">
          <div class="bench-header">
            <span class="bench-ticker">{ticker}</span>
            <span class="bench-label">{label}</span>
          </div>
          <table>
            <tr><td>ETF P&amp;L</td>
                <td class="num {sign_class(b['pnl'])}">{fmt_gbp(b['pnl'])}</td></tr>
            <tr><td>ETF Final Value</td>
                <td class="num">{fmt_gbp(b['final_value'])}</td></tr>
            <tr><td>ETF Total Return</td>
                <td class="num {sign_class(b['total_return'])}">{fmt_pct(b['total_return'])}</td></tr>
            <tr><td>ETF XIRR</td>
                <td class="num">{b_irr_str}</td></tr>
            <tr><td>Our XIRR</td>
                <td class="num gold"><strong>{irr_pct}</strong></td></tr>
            <tr><td>Outperformance</td>
                <td class="num {op_class}"><strong>{op_str}</strong></td></tr>
          </table>
        </div>"""

    benchmark_cards_html = "\n".join(benchmark_card(b) for b in benchmarks)

    if sub_accounts and sub_account_rows:
        sub_account_section = f"""  <section class="full">
    <h2>Sub-Account Breakdown</h2>
    <table class="sub-account-table">
      <thead>
        <tr>
          <th>Account Holder</th>
          <th>Account Type</th>
          <th class="num">Invested</th>
          <th class="num">Cash</th>
          <th class="num">Total Value</th>
          <th class="num">P&amp;L</th>
          <th class="num">Total Return</th>
          <th class="num">XIRR</th>
        </tr>
      </thead>
      <tbody>{sub_account_rows}
      </tbody>
    </table>
  </section>"""
    else:
        sub_account_section = ""

    # ── Outperformance banner items ───────────────────────────────────────────
    def banner(b):
        ticker = b["ticker"]
        label  = TICKER_LABELS.get(ticker, ticker)
        op     = outperform(b["irr"])
        op_class = sign_class(op)
        op_str   = f"{op * 100:+.2f}%" if op is not None else "N/A"
        return f"""
        <div class="banner-item">
          <div class="banner-label">{label}</div>
          <div class="banner-value {op_class}">{op_str}</div>
        </div>"""

    banners_html = "\n".join(banner(b) for b in benchmarks)

    n_bench = len(benchmarks)
    bench_cols = f"repeat({min(n_bench, 3)}, 1fr)"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Housemartin Returns Report — {now}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --ink:    #0d0d0d;
    --paper:  #f5f0e8;
    --cream:  #ede8dc;
    --rule:   #c8bfa8;
    --gold:   #b5892a;
    --green:  #2a6e47;
    --red:    #8b2a2a;
    --muted:  #6b6255;
  }}

  body {{
    background: var(--paper);
    color: var(--ink);
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    line-height: 1.6;
    min-height: 100vh;
  }}

  header {{
    border-bottom: 3px double var(--ink);
    padding: 3rem 4rem 2rem;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: end;
    gap: 2rem;
  }}
  .masthead-title {{
    font-family: 'Playfair Display', serif;
    font-size: 3.2rem;
    font-weight: 900;
    letter-spacing: -0.02em;
    line-height: 1;
  }}
  .masthead-sub {{
    font-size: 0.7rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--muted);
    margin-top: 0.5rem;
  }}
  .masthead-date {{
    text-align: right;
    font-size: 0.7rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    line-height: 2;
  }}

  main {{
    padding: 3rem 4rem;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2.5rem;
  }}
  .full {{ grid-column: 1 / -1; }}

  section {{
    border: 1px solid var(--rule);
    background: var(--cream);
    padding: 1.5rem 2rem;
  }}
  section h2 {{
    font-family: 'Playfair Display', serif;
    font-size: 0.65rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--rule);
    padding-bottom: 0.5rem;
    margin-bottom: 1.2rem;
  }}

  .hero-number {{
    font-family: 'Playfair Display', serif;
    font-size: 3.8rem;
    font-weight: 700;
    line-height: 1;
    margin: 0.5rem 0;
  }}
  .hero-label {{
    font-size: 0.65rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--muted);
  }}
  .xirr-hero-inner {{
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 2rem;
  }}
  .hero-nav {{
    text-align: right;
    border-left: 1px solid var(--rule);
    padding-left: 2rem;
  }}
  .hero-nav .hero-number {{
    font-size: 3.8rem;
  }}

  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 0.5rem 0; }}
  td:first-child {{ color: var(--muted); font-size: 0.75rem; letter-spacing: 0.05em; }}
  td.num {{ text-align: right; font-weight: 500; }}
  tr + tr td {{ border-top: 1px solid var(--rule); }}

  /* Outperformance banner strip */
  .banner-strip {{
    background: var(--ink);
    color: var(--paper);
    padding: 1.4rem 2rem;
    display: flex;
    align-items: center;
    gap: 0;
    flex-wrap: wrap;
  }}
  .banner-strip-label {{
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    opacity: 0.45;
    margin-right: 2.5rem;
    white-space: nowrap;
    line-height: 1.6;
  }}
  .banner-item {{
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    padding: 0 2rem;
    border-left: 1px solid rgba(255,255,255,0.12);
  }}
  .banner-label {{
    font-size: 0.6rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    opacity: 0.45;
    margin-bottom: 0.1rem;
  }}
  .banner-value {{
    font-family: 'Playfair Display', serif;
    font-size: 1.65rem;
    font-weight: 700;
    line-height: 1;
  }}
  .banner-value.pos {{ color: #7ec8a0; }}
  .banner-value.neg {{ color: #e08080; }}

  /* Benchmark cards */
  .bench-grid {{
    display: grid;
    grid-template-columns: {bench_cols};
    gap: 1.5rem;
  }}
  .bench-card {{
    border: 1px solid var(--rule);
    background: var(--paper);
    padding: 1.25rem 1.5rem;
  }}
  .bench-header {{
    margin-bottom: 1rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid var(--rule);
  }}
  .bench-ticker {{
    font-family: 'Playfair Display', serif;
    font-size: 1.05rem;
    font-weight: 700;
    display: block;
  }}
  .bench-label {{
    font-size: 0.65rem;
    color: var(--muted);
    letter-spacing: 0.05em;
  }}

  .account-table td:first-child {{ color: var(--ink); }}
  .sub-account-table th {{ color: var(--muted); font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase; padding-bottom: 0.6rem; text-align: left; border-bottom: 1px solid var(--rule); }}
  .sub-account-table th.num {{ text-align: right; }}
  .sub-account-table td {{ padding: 0.45rem 0; }}
  .sub-account-table td:first-child, .sub-account-table td:nth-child(2) {{ color: var(--ink); }}
  .pos  {{ color: var(--green); }}
  .neg  {{ color: var(--red); }}
  .gold {{ color: var(--gold); }}

  footer {{
    border-top: 1px solid var(--rule);
    padding: 1.25rem 4rem;
    font-size: 0.65rem;
    color: var(--muted);
    letter-spacing: 0.05em;
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem;
  }}

  @media (max-width: 900px) {{
    header, main {{ padding: 1.5rem; }}
    main {{ grid-template-columns: 1fr; }}
    .full {{ grid-column: auto; }}
    .masthead-title {{ font-size: 2rem; }}
    .bench-grid {{ grid-template-columns: 1fr; }}
    .banner-strip {{ flex-direction: column; gap: 1rem; }}
    .banner-item {{ border-left: none; padding: 0; }}
  }}
</style>
</head>
<body>

<header>
  <div>
    <div class="masthead-title">Housemartin<br>Returns Report</div>
    <div class="masthead-sub">Family Portfolio · Consolidated</div>
  </div>
  <div class="masthead-date">Generated<br>{now}</div>
</header>

<main>

  <!-- XIRR hero — most important figure, shown first full-width -->
  <section class="full xirr-hero">
    <div class="xirr-hero-inner">
      <div>
        <div class="hero-label">Annualised Return (XIRR)</div>
        <div class="hero-number {sign_class(irr_result)} gold">{irr_pct}</div>
      </div>
      {f'''<div class="hero-nav">
        <div class="hero-label">HMFUND NAV</div>
        <div class="hero-number gold">{hmfund_nav:.4f}</div>
      </div>''' if hmfund_nav else ""}
    </div>
  </section>

  <!-- Portfolio summary + account breakdown side by side -->
  <section>
    <h2>Portfolio Returns</h2>
    <table>
      <tr><td>Net Invested</td>
          <td class="num">{fmt_gbp(net_investment)}</td></tr>
      <tr><td>Current Value</td>
          <td class="num">{fmt_gbp(current_value)}</td></tr>
      <tr><td>P&amp;L</td>
          <td class="num {sign_class(pnl)}">{fmt_gbp(pnl)}</td></tr>
      <tr><td>Total Return</td>
          <td class="num {sign_class(total_return)}">{fmt_pct(total_return)}</td></tr>
      <tr><td>Value-weighted avg hold</td>
          <td class="num">{fmt_time(time_difference)}</td></tr>
    </table>
  </section>

  <section>
    <h2>Account Breakdown</h2>
    <table class="account-table">
      {account_rows}
      <tr>
        <td><strong>Total</strong></td>
        <td class="num"><strong>{fmt_gbp(total_balance)}</strong></td>
      </tr>
    </table>
  </section>

  {sub_account_section}

  <section class="full">
    <h2>Benchmark Comparisons</h2>
    <div class="bench-grid">
      {benchmark_cards_html}
    </div>
  </section>

</main>

<footer>
  <span>Housemartin Portfolio Automation · Auto-generated</span>
  <span>XIRR via scipy.optimize.brentq · Benchmarks: {" · ".join(b["ticker"] for b in benchmarks)}</span>
</footer>

</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")
    print(f"  → HTML report saved: {output_path}")
    return output_path
