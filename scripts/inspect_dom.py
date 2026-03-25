"""Quick DOM inspection of Action Network public betting page."""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(
        "https://www.actionnetwork.com/ncaab/public-betting",
        timeout=30000,
        wait_until="networkidle",
    )
    page.wait_for_timeout(5000)

    # Basic element counts
    rows = page.query_selector_all("tr")
    print(f"Found {len(rows)} <tr> elements")

    tables = page.query_selector_all("table")
    print(f"Found {len(tables)} <table> elements")

    data_els = page.query_selector_all("[data-testid]")
    print(f"Found {len(data_els)} [data-testid] elements")
    for el in data_els[:30]:
        tid = el.get_attribute("data-testid")
        tag = el.evaluate("el => el.tagName")
        print(f"  <{tag}> data-testid={tid!r}")

    # Get first few <tr> outerHTML to see the row structure
    print("\n" + "=" * 80)
    for i, row in enumerate(rows[:5]):
        outer = row.evaluate("el => el.outerHTML")
        print(f"\n--- TR #{i} (first 3000 chars) ---")
        print(outer[:3000])
        print()

    # Also look for any div-based game rows
    print("=" * 80)
    print("\nLooking for class patterns...")
    # Search for elements with 'game' in classname
    game_els = page.evaluate("""
        () => {
            const all = document.querySelectorAll('[class*="game"], [class*="Game"], [class*="matchup"], [class*="Matchup"], [class*="row"], [class*="Row"]');
            return Array.from(all).slice(0, 15).map(el => ({
                tag: el.tagName,
                cls: el.className.substring(0, 200),
                testid: el.getAttribute('data-testid') || '',
                childCount: el.children.length,
            }));
        }
    """)
    for g in game_els:
        print(f"  <{g['tag']}> class={g['cls']!r} testid={g['testid']!r} children={g['childCount']}")

    browser.close()
    print("\nDone.")
