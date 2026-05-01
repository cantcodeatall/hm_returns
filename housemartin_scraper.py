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
    email    = account["email"].strip()
    # Strip surrounding quotes dotenv may leave when password contains special chars (e.g. $)
    password = account["password"].strip().strip("'\"")
    page.fill(email_sel, email)
    page.fill('input[type="password"]', password)
    page.click('button[type="submit"]')

    try:
        page.wait_for_url(lambda url: "log-in" not in url and "login" not in url, timeout=20_000)
    except PlaywrightTimeout:
        page.wait_for_load_state("domcontentloaded", timeout=20_000)

    print("  → Logged in successfully")
    page.wait_for_timeout(1_000)
    dismiss_popup(page)

    # ── 2. Scrape balance ─────────────────────────────────────────────────────
    # Dashboard has account-type tabs top-right: "All accounts", "Regular account", "ISA account"
    # We read the total platform balance from each tab to get per-sub-account values.
    print("  → Scraping balances per account tab...")

    def read_total_platform_balance_text() -> str:
        """
        Return the £ amount from the 'Total platform balance' dashboard card.
        Uses JS to find the most specific element: one whose label text is exactly
        'Total platform balance' and whose sibling/parent contains the £ value.
        """
        try:
            result = page.evaluate("""() => {
                // Walk all elements looking for one whose text is exactly the label
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const txt = (el.textContent || '').trim();
                    if (txt.toLowerCase() === 'total platform balance') {
                        // Found the label — look in parent/sibling for the £ value
                        const container = el.closest('[class*="card"], [class*="Card"]') || el.parentElement;
                        if (container) {
                            const match = container.textContent.match(/£([0-9,]+[.]?[0-9]*)/);
                            if (match) return '£' + match[1];
                        }
                    }
                }
                // Fallback: find any element containing ONLY "Total platform balance"
                // and a £ amount (no other card labels present)
                for (const el of all) {
                    const txt = (el.textContent || '').trim();
                    if (txt.toLowerCase().includes('total platform balance') &&
                        !txt.toLowerCase().includes('gross investment') &&
                        !txt.toLowerCase().includes('cash') &&
                        !txt.toLowerCase().includes('unmatched')) {
                        const match = txt.match(/£([0-9,]+[.]?[0-9]*)/);
                        if (match) return '£' + match[1];
                    }
                }
                return null;
            }""")
            return result or ""
        except Exception:
            return ""

    def read_card_amount(label: str) -> float:
        """Return the £ amount from the dashboard card whose label matches exactly."""
        try:
            result = page.evaluate("""(lbl) => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    if ((el.textContent || '').trim().toLowerCase() === lbl.toLowerCase()) {
                        const container = el.closest('[class*="card"], [class*="Card"]') || el.parentElement;
                        if (container) {
                            const match = container.textContent.match(/£([0-9,]+[.]?[0-9]*)/);
                            if (match) return parseFloat(match[1].replace(/,/g, ''));
                        }
                    }
                }
                return null;
            }""", label)
            return float(result) if result is not None else 0.0
        except Exception:
            return 0.0

    def scrape_tab_balance(tab_label: str) -> tuple:
        """Click a dashboard tab, wait for balance to update.
        Returns (total_balance, pending_withdrawal, gross_investment, cash)."""
        try:
            # Read the current balance BEFORE clicking so we can wait for it to change
            balance_before = read_total_platform_balance_text()

            # Dump all candidates for this label so we can see what's being found
            candidates_info = page.evaluate("""(label) => {
                const results = [];
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const txt = (el.textContent || '').trim();
                    if (txt === label) {
                        results.push({
                            tag: el.tagName,
                            cls: el.className,
                            children: el.children.length,
                            visible: el.offsetParent !== null,
                        });
                    }
                }
                return results;
            }""", tab_label)
            print(f"  → Tab '{tab_label}' candidates: {candidates_info}")

            # Click the <a class="summary-account-select"> tab link directly
            clicked = page.evaluate("""(label) => {
                // First try: exact match on the known anchor class
                const anchors = document.querySelectorAll('a.summary-account-select, a.nav-link');
                for (const el of anchors) {
                    if ((el.textContent || '').trim() === label) {
                        el.click();
                        return el.tagName + '.' + el.className;
                    }
                }
                // Fallback: any <a> or <button> with exact text
                for (const tag of ['a', 'button']) {
                    for (const el of document.querySelectorAll(tag)) {
                        if ((el.textContent || '').trim() === label && el.offsetParent !== null) {
                            el.click();
                            return el.tagName + '.' + el.className;
                        }
                    }
                }
                return null;
            }""", tab_label)

            if not clicked:
                print(f"  ⚠  Tab '{tab_label}' not found via JS click, skipping")
                return None, 0.0, 0.0, 0.0
            print(f"  → Clicked: {clicked}")

            # Wait until the Total platform balance card shows a DIFFERENT value
            import time
            for _ in range(40):   # up to 4s
                time.sleep(0.1)
                current = read_total_platform_balance_text()
                if current and current != balance_before:
                    break
            else:
                print(f"  ⚠  Balance did not change after clicking '{tab_label}' (was {balance_before})")

        except Exception as e:
            print(f"  ⚠  Tab '{tab_label}' click failed: {e}")
            return None, 0.0, 0.0, 0.0

        # Read "Total platform balance" using the same targeted JS as read_total_platform_balance_text
        raw = read_total_platform_balance_text()
        bal = clean_amount(raw) if raw else None

        if bal is None:
            return None, 0.0, 0.0, 0.0

        # Pending withdrawal for this tab
        pend = 0.0
        withdrawal_phrases = ["Pending withdrawal", "pending withdrawal",
                               "awaiting authorisation", "awaiting authorization",
                               "Withdrawal request"]
        for phrase in withdrawal_phrases:
            for el in page.query_selector_all(f"*:has-text('{phrase}')"):
                txt = el.inner_text()
                for line in txt.splitlines():
                    if phrase.lower() in line.lower():
                        hits = re.findall(r'£[\d,]+\.?\d*', line)
                        if hits:
                            pend = abs(clean_amount(hits[0]))
                            break
                if pend:
                    break
            if pend:
                break

        # Read gross investment and cash for this tab
        gross_investment = read_card_amount("Gross investment")
        cash             = read_card_amount("Cash") + pend   # include pending withdrawal in cash

        return bal, pend, gross_investment, cash

    # Discover account-type tabs via JS — find buttons whose EXACT text is short
    # (avoids matching large container elements that also contain "account")
    tab_balances = {}

    raw_tabs = page.evaluate("""() => {
        const results = [];
        // Cast a wider net — include any clickable element with short account-related text
        const candidates = document.querySelectorAll(
            'button, [role="tab"], [role="button"], a, li, span, div'
        );
        for (const el of candidates) {
            // Use textContent to avoid hidden elements returning empty innerText
            const txt = (el.textContent || '').trim();
            if (txt.length > 0 && txt.length < 25 &&
                txt.toLowerCase().includes('account') &&
                !txt.toLowerCase().includes('bank') &&
                !txt.toLowerCase().includes('manage') &&
                !txt.toLowerCase().includes('settings')) {
                results.push(txt);
            }
        }
        return [...new Set(results)];
    }""")
    print(f"  → Dashboard account elements found: {raw_tabs}")

    # Known tab label patterns from the Housemartin dashboard
    KNOWN_SUB_TABS = ["Regular account", "ISA account", "IFISA account", "Innovative Finance ISA"]
    KNOWN_ALL_TABS = ["All accounts"]

    all_label_tab  = next((t for t in raw_tabs if t in KNOWN_ALL_TABS), None) or \
                     next((t for t in raw_tabs if "all" in t.lower()), None)
    sub_tab_labels = [t for t in raw_tabs if t in KNOWN_SUB_TABS] or \
                     [t for t in raw_tabs if "all" not in t.lower() and
                      "account" in t.lower() and len(t) < 25]

    # Read whatever balance is currently shown before any clicking
    current_shown_text = read_total_platform_balance_text()

    # tab_details stores {label: {total, gross_investment, cash}} for report
    tab_details = {}

    # Scrape each sub-account tab
    for tab_label in sub_tab_labels:
        tab_bal, tab_pend, tab_gross, tab_cash = scrape_tab_balance(tab_label)
        if tab_bal is None and current_shown_text:
            tab_bal   = clean_amount(current_shown_text)
            tab_pend  = 0.0
            tab_gross = read_card_amount("Gross investment")
            tab_cash  = read_card_amount("Cash") + tab_pend
            print(f"  → {tab_label}: £{tab_bal:,.2f} (was already active tab)")
        if tab_bal is not None:
            tab_balances[tab_label] = tab_bal + tab_pend
            tab_details[tab_label]  = {
                "total":            tab_bal + tab_pend,
                "gross_investment": tab_gross,
                "cash":             tab_cash,
            }
            print(f"  → {tab_label}: total £{tab_balances[tab_label]:,.2f}  "
                  f"(invested £{tab_gross:,.2f} + cash £{tab_cash:,.2f})")

    # Get "All accounts" total
    all_bal, all_pend = None, 0.0
    all_gross, all_cash = 0.0, 0.0
    if all_label_tab:
        all_bal, all_pend, all_gross, all_cash = scrape_tab_balance(all_label_tab)

    # No "All accounts" tab — derive total from sum of sub-accounts
    if all_bal is None and tab_balances:
        all_bal  = sum(tab_balances.values())
        all_pend = 0.0
        print(f"  → No 'All accounts' tab — derived total: £{all_bal:,.2f}")

    # Last resort — use whatever the page is currently showing
    if all_bal is None and current_shown_text:
        all_bal  = clean_amount(current_shown_text)
        all_pend = 0.0
        print(f"  → Balance from current view: £{all_bal:,.2f}")

    if not all_bal:
        save_debug_screenshot(page, name, "balance")
        raise RuntimeError(f"Balance not found for {name}")

    platform_balance = all_bal
    print(f"  → Total platform balance: £{platform_balance:,.2f}")

    # ── Sanity check: sub-account balances should sum to total ────────────────
    if tab_balances and len(tab_balances) > 1:
        sub_total = sum(tab_balances.values())
        expected  = platform_balance + all_pend
        diff      = abs(sub_total - expected)
        if diff > 1.0:
            print(f"  ⚠  Sub-account sum £{sub_total:,.2f} ≠ total £{expected:,.2f} (diff £{diff:,.2f}) — tab reads may be wrong")
        else:
            parts = " + ".join(f"{k} £{v:,.2f}" for k, v in tab_balances.items())
            print(f"  ✓  Sub-account check passed: {parts} = £{sub_total:,.2f}")

    # ── 3. Pending withdrawal already captured per-tab above ─────────────────
    pending_withdrawal = all_pend
    if pending_withdrawal:
        print(f"  → Pending withdrawal: £{pending_withdrawal:,.2f}")

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

    all_label      = next((o for o in opts if "all"     in o.lower()), None)
    sub_labels     = [o for o in opts if "all" not in o.lower()]  # e.g. ["Regular account","ISA account"]

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

    def do_export(label: str, file_suffix: str) -> Path:
        """Select `label` in dropdown, Apply, then export CSV. Returns saved path."""
        ngselect_pick(label)
        click_apply()
        print(f"  → Exporting '{label}'...")
        wait_for_spinner()
        kill_uf()
        page.wait_for_timeout(500)

        out_path  = STAGING_DIR / f"{name.replace(' ', '_')}_{file_suffix}.csv"
        blob_data = None
        for attempt in range(1, 4):
            print(f"  → Export attempt {attempt}/3...")
            export_btn = page.wait_for_selector(
                'button:has-text("Export to CSV")',
                timeout=15_000, state="visible"
            )
            page.evaluate(BLOB_JS)
            page.wait_for_timeout(300)
            export_btn.click(force=True)
            for i in range(60):
                result = page.evaluate("() => window.__blobData")
                if result:
                    blob_data = result
                    break
                time.sleep(0.5)
                if i % 10 == 9:
                    print(f"  → Still waiting... ({(i+1)//2}s)")
            if blob_data:
                break
            print(f"  ⚠  Attempt {attempt} got nothing, retrying...")
            page.wait_for_timeout(2_000)

        if not blob_data:
            save_debug_screenshot(page, name, f"export_fallback_{file_suffix}")
            raise RuntimeError(f"Export failed for {name}/{label} after 3 attempts")

        _, encoded = blob_data.split(",", 1)
        out_path.write_bytes(base64.b64decode(encoded))
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"CSV empty or missing: {out_path}")
        print(f"  → Saved: {out_path} ({out_path.stat().st_size:,} bytes)")
        return out_path

    # ── 7. Export per sub-account + All accounts ──────────────────────────────
    sub_account_csvs = []   # [{label, csv_path}]

    if not sub_labels:
        # Only one account type — export as-is
        print("  → Single account type, applying and exporting...")
        click_apply()
        csv_path = do_export(opts[0] if opts else "account",
                             "transactions")
        sub_account_csvs.append({"label": opts[0] if opts else name,
                                  "csv_path": csv_path})
    else:
        # Export each sub-account individually
        for sub in sub_labels:
            suffix   = sub.lower().replace(" ", "_").replace("account", "acc").strip("_")
            csv_path = do_export(sub, suffix)
            sub_account_csvs.append({"label": sub, "csv_path": csv_path})

        # Also export All accounts (used for merged XIRR calculation)
        if all_label:
            all_csv = do_export(all_label, "all")
        else:
            all_csv = sub_account_csvs[-1]["csv_path"]

    # The "all" CSV is what gets merged for XIRR — use it as the primary csv_path
    csv_path = all_csv if (sub_labels and all_label) else sub_account_csvs[0]["csv_path"]
    print(f"  → All exports complete")

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

    return {"name": name, "balance": total_balance, "csv_path": csv_path, "sub_accounts": sub_account_csvs, "tab_balances": tab_balances, "tab_details": tab_details}


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
