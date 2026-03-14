"""
housemartin_scraper.py
Logs into Housemartin accounts, scrapes balances, and downloads transaction CSVs.
"""

import os
import re
import shutil
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────

ACCOUNTS = [
    {
        "name":     os.getenv("HM_ACCOUNT1_NAME", "Account 1"),
        "email":    os.getenv("HM_ACCOUNT1_EMAIL"),
        "password": os.getenv("HM_ACCOUNT1_PASSWORD"),
    },
    {
        "name":     os.getenv("HM_ACCOUNT2_NAME", "Account 2"),
        "email":    os.getenv("HM_ACCOUNT2_EMAIL"),
        "password": os.getenv("HM_ACCOUNT2_PASSWORD"),
    },
    {
        "name":     os.getenv("HM_ACCOUNT3_NAME", "Account 3"),
        "email":    os.getenv("HM_ACCOUNT3_EMAIL"),
        "password": os.getenv("HM_ACCOUNT3_PASSWORD"),
    },
]

BASE_URL        = "https://portal.housemartin.co"
LOGIN_URL       = f"{BASE_URL}/log-in"
TRANSACTIONS_URL = f"{BASE_URL}/my/reports/cash-acc-statement"
STAGING_DIR     = Path("hm_staging")


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_amount(text: str) -> float:
    """Strip £, commas and whitespace, return float."""
    return float(text.replace("£", "").replace(",", "").strip())


def dismiss_popup(page):
    """
    Dismiss the Userflow onboarding widget (class: uf-tour-selection / uf-checklist).
    Strategy 1: hide via JS (works even when widget intercepts pointer events).
    Strategy 2: click its close button.
    Safe to call at any time.
    """
    # Strategy 1: JS hide — bypasses pointer-event interception entirely
    try:
        hidden = page.evaluate("""() => {
            const selectors = [
                '.uf-tour-selection',
                '.uf-checklist',
                '[data-uf-content="checklist"]',
                '[class*="uf-notification"]',
            ];
            let found = false;
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(el => {
                    el.style.display = 'none';
                    found = true;
                });
            }
            return found;
        }""")
        if hidden:
            page.wait_for_timeout(200)
            print("  → Userflow widget hidden via JS")
            return
    except Exception:
        pass

    # Strategy 2: click close button
    for sel in ['[data-uf-dismiss]', '[class*="uf-dismiss"]', '[class*="uf-close"]',
                'button:has-text("×")', 'button:has-text("✕")',
                '[aria-label="Close"]', '[aria-label="close"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(force=True)
                page.wait_for_timeout(400)
                print(f"  → Popup closed via '{sel}'")
                return
        except Exception:
            continue


def save_debug_screenshot(page, name, label):
    path = STAGING_DIR / f"{name}_{label}_debug.png"
    page.screenshot(path=str(path))
    print(f"  ⚠  Screenshot saved: {path}")
    return path


# ── Main scrape logic ─────────────────────────────────────────────────────────

def scrape_account(page, account: dict) -> dict:
    name = account["name"]
    print(f"\n{'─'*50}")
    print(f"  Processing: {name}")
    print(f"{'─'*50}")

    # ── 1. Login ──────────────────────────────────────────────────────────────
    print("  → Navigating to login page...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

    email_sel = 'input[type="email"], input[name="email"], input[placeholder*="email" i]'
    page.wait_for_selector(email_sel, state="visible", timeout=20_000)
    page.fill(email_sel, account["email"])
    page.fill('input[type="password"]', account["password"])
    page.click('button[type="submit"]')

    try:
        page.wait_for_url(lambda url: "log-in" not in url and "login" not in url, timeout=20_000)
    except PlaywrightTimeout:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)

    print("  → Logged in successfully")
    page.wait_for_timeout(1_000)
    dismiss_popup(page)

    # ── 2. Scrape balance ─────────────────────────────────────────────────────
    # Dashboard has 4 summary cards. We want the largest £ value = total portfolio.
    print("  → Scraping balance...")

    card_sel = "[class*='card'] h3, [class*='Card'] h3, [class*='card'] h2, [class*='Card'] h2"
    els = page.query_selector_all(card_sel)
    amounts_found = []
    for el in els:
        txt = el.inner_text().strip()
        if txt.startswith("£"):
            try:
                amounts_found.append((clean_amount(txt), txt))
            except ValueError:
                pass

    balance_text = None
    if amounts_found:
        print(f"  → Card amounts: {[t for _, t in amounts_found]}")
        amounts_found.sort(key=lambda x: x[0], reverse=True)
        balance_text = amounts_found[0][1]

    # Fallback: scan whole page, take largest £ value
    if balance_text is None:
        print("  → Fallback: scanning all £ amounts...")
        seen, candidates = set(), []
        for el in page.query_selector_all("*"):
            try:
                txt = el.inner_text().strip()
                if txt.startswith("£") and "\n" not in txt[:20] and txt not in seen:
                    seen.add(txt)
                    candidates.append((clean_amount(txt), txt))
            except Exception:
                pass
        if candidates:
            candidates.sort(reverse=True)
            balance_text = candidates[0][1]
            print(f"  → Top values: {[t for _, t in candidates[:5]]}")

    if balance_text is None:
        save_debug_screenshot(page, name, "balance")
        raise RuntimeError(f"Balance not found for {name}")

    platform_balance = clean_amount(balance_text)
    print(f"  → Platform balance: £{platform_balance:,.2f}")

    # ── 3. Check for pending withdrawal ───────────────────────────────────────
    pending_withdrawal = 0.0
    try:
        # Catch both "Pending withdrawal" and "Withdrawal requests awaiting authorisation".
        # We look for the £ amount that appears ON THE SAME LINE as the phrase,
        # not the largest amount in the whole element (which may be a big container).
        withdrawal_phrases = [
            "Pending withdrawal",
            "pending withdrawal",
            "awaiting authorisation",
            "awaiting authorization",
            "Withdrawal request",
        ]
        for phrase in withdrawal_phrases:
            for el in page.query_selector_all(f"*:has-text('{phrase}')"):
                txt = el.inner_text()
                # Find the line that contains the phrase, extract £ from that line only
                for line in txt.splitlines():
                    if phrase.lower() in line.lower():
                        hits = re.findall(r'£[\d,]+\.?\d*', line)
                        if hits:
                            pending_withdrawal = abs(clean_amount(hits[0]))
                            print(f"  → Withdrawal pending ('{phrase}'): £{pending_withdrawal:,.2f}  [line: {line.strip()[:80]}]")
                            break
                if pending_withdrawal:
                    break
            if pending_withdrawal:
                break
    except Exception:
        pass

    total_balance = platform_balance + pending_withdrawal
    if pending_withdrawal:
        print(f"  → Adjusted total: £{total_balance:,.2f}")

    # ── 4. Navigate directly to Transactions page ─────────────────────────────
    # We know the URL from the screenshot — navigate directly, skip menu clicks
    print(f"  → Navigating to transactions page...")
    page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1_500)   # let JS render the filters
    dismiss_popup(page)            # kill popup before touching anything
    # Press Escape once more to ensure any stray dropdowns/menus are closed
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    print(f"  → On page: {page.url}")

    # ── Dump full page HTML for selector debugging ───────────────────────────
    import pathlib
    pathlib.Path("hm_staging").mkdir(exist_ok=True)
    full_html = page.content()
    with open('hm_staging/filter_html_dump.txt', 'w', encoding='utf-8') as f:
        f.write(full_html)
    print(f"  → Full page HTML dumped ({len(full_html):,} bytes) to hm_staging/filter_html_dump.txt")

    # ── 5 & 6. Select "All accounts" via ng-select + Apply ──────────────────
    # Site updated: dropdown is now Angular ng-select (not Select2).
    # Trigger: app-select-account .ng-select-container
    # Options: .ng-option elements in a panel appended to body

    APPLY_SEL = 'button:has-text("Apply")'

    def kill_uf():
        page.evaluate("""document.querySelectorAll('[class*="uf-"]').forEach(e=>e.style.display='none')""")

    def wait_for_spinner():
        """Wait until the block-ui loading spinner is gone."""
        try:
            page.wait_for_selector(
                "div.block-ui-wrapper.root.active",
                state="hidden", timeout=30_000
            )
            print("  → Spinner gone")
        except PlaywrightTimeout:
            pass  # spinner may never have appeared

    def click_apply():
        kill_uf()
        page.wait_for_timeout(300)
        btn = page.wait_for_selector(APPLY_SEL, timeout=10_000, state="visible")
        try:
            btn.click(timeout=3_000)
        except Exception:
            page.evaluate("el => el.click()", btn)
        print("  → Apply clicked, waiting for spinner...")
        wait_for_spinner()
        kill_uf()
        try:
            page.wait_for_selector("table tbody tr", timeout=20_000, state="visible")
            print("  → Table rows visible")
        except PlaywrightTimeout:
            page.wait_for_timeout(2_000)

    def ngselect_pick(label):
        """Open the ng-select account dropdown and click the matching option."""
        kill_uf()
        trigger = page.wait_for_selector(
            "app-select-account .ng-select-container",
            timeout=10_000, state="visible"
        )
        trigger.click()
        page.wait_for_timeout(600)

        # ng-select renders options in a panel; try body-level and local
        opt = page.wait_for_selector(
            f'.ng-option:has-text("{label}")',
            timeout=5_000, state="visible"
        )
        opt.click()
        page.wait_for_timeout(500)

        try:
            shown = page.inner_text("app-select-account .ng-value-label")
            print(f"  → ng-select now shows: '{shown.strip()}'")
        except Exception:
            pass

    # Read available options by opening the dropdown briefly
    kill_uf()
    trigger = page.wait_for_selector(
        "app-select-account .ng-select-container",
        timeout=10_000, state="visible"
    )
    trigger.click()
    page.wait_for_timeout(600)
    option_els = page.query_selector_all(".ng-option")
    opts = [el.inner_text().strip() for el in option_els if el.inner_text().strip()]
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    print(f"  → Account options: {opts}")

    all_label = next((o for o in opts if "all" in o.lower()), None)

    if not all_label:
        print("  → Single account only, applying as-is")
        click_apply()
    else:
        specific = next((o for o in opts if "all" not in o.lower()), opts[0])
        print(f"  → Step 5a: picking '{specific}'")
        ngselect_pick(specific)
        click_apply()

        print(f"  → Step 5b: picking '{all_label}'")
        ngselect_pick(all_label)
        click_apply()

    print("  → Transactions loaded, ready to export")

    # ── 7. Export to CSV ──────────────────────────────────────────────────────
    # Wait for spinner to fully clear before exporting
    print("  → Locating Export to CSV button...")
    wait_for_spinner()
    kill_uf()
    page.wait_for_timeout(500)

    import time, base64

    BLOB_JS = """() => {
        window.__blobData = null;
        window.__blobRef  = null;
        const origCreateURL = URL.createObjectURL.bind(URL);
        URL.createObjectURL = function(blob) {
            window.__blobRef = blob;
            return origCreateURL(blob);
        };
        const origCreateEl = document.createElement.bind(document);
        document.createElement = function(tag) {
            const el = origCreateEl(tag);
            if (tag.toLowerCase() === 'a') {
                const origClick = el.click.bind(el);
                el.click = function() {
                    const blob = window.__blobRef;
                    if (blob) {
                        const reader = new FileReader();
                        reader.onloadend = () => { window.__blobData = reader.result; };
                        reader.readAsDataURL(blob);
                    } else if (el.href && el.href.startsWith('blob:')) {
                        fetch(el.href).then(r => r.blob()).then(b => {
                            const reader = new FileReader();
                            reader.onloadend = () => { window.__blobData = reader.result; };
                            reader.readAsDataURL(b);
                        });
                    }
                    return origClick();
                };
            }
            return el;
        };
    }"""

    csv_path = STAGING_DIR / f"{name.replace(' ', '_')}_transactions.csv"
    blob_data = None

    for attempt in range(1, 4):   # up to 3 attempts
        print(f"  → Export attempt {attempt}/3 — injecting blob interceptor...")

        export_btn = page.wait_for_selector(
            'button:has-text("Export to CSV")',
            timeout=15_000, state="visible"
        )

        # Re-inject interceptor fresh each attempt (resets __blobData)
        page.evaluate(BLOB_JS)
        page.wait_for_timeout(300)   # brief pause so patch is in place before click

        export_btn.click(force=True)
        print(f"  → Export clicked, waiting for blob data...")

        for i in range(60):   # poll up to 30s
            result = page.evaluate("() => window.__blobData")
            if result:
                blob_data = result
                break
            time.sleep(0.5)
            if i % 10 == 9:
                print(f"  → Still waiting... ({(i+1)//2}s)")

        if blob_data:
            break
        print(f"  ⚠  Attempt {attempt} got no blob data, retrying...")
        page.wait_for_timeout(2_000)

    if blob_data:
        print("  → Blob intercepted, saving file...")
        header, encoded = blob_data.split(",", 1)
        csv_path.write_bytes(base64.b64decode(encoded))
    else:
        save_debug_screenshot(page, name, "export_fallback")
        raise RuntimeError(f"Export failed for {name} after 3 attempts — no blob data captured")

    if not csv_path.exists() or csv_path.stat().st_size == 0:
        raise RuntimeError(f"CSV file empty or missing: {csv_path}")
    print(f"  → CSV saved: {csv_path} ({csv_path.stat().st_size:,} bytes)")

    # ── 8. Logout ─────────────────────────────────────────────────────────────
    try:
        for sel in ["a[href*='logout']", "a[href*='log-out']",
                    "button:has-text('Log out')", "a:has-text('Log out')",
                    "a:has-text('Sign out')"]:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
                break
    except Exception:
        pass

    return {"name": name, "balance": total_balance, "csv_path": csv_path}


# ── Runner ────────────────────────────────────────────────────────────────────

def run_scraper(headless: bool = True) -> list:
    STAGING_DIR.mkdir(exist_ok=True)
    results = []
    with sync_playwright() as p:
        for account in ACCOUNTS:
            if not account["email"] or not account["password"]:
                print(f"  ⚠  Skipping {account['name']}: credentials not set in .env")
                continue
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            try:
                result = scrape_account(page, account)
                results.append(result)
            except Exception as e:
                print(f"  ✗  Error processing {account['name']}: {e}")
                raise
            finally:
                context.close()
                browser.close()
    return results


if __name__ == "__main__":
    import sys
    headless = "--visible" not in sys.argv
    results = run_scraper(headless=headless)
    print("\n── Summary ──")
    for r in results:
        print(f"  {r['name']}: £{r['balance']:,.2f}")
    print(f"  TOTAL: £{sum(r['balance'] for r in results):,.2f}")
