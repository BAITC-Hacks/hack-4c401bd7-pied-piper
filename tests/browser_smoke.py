"""Optional real-browser check against the synthetic fixture (requires Playwright)."""
import argparse
from pathlib import Path
import time
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8501')
    parser.add_argument('--screenshot', type=Path, default=Path('/tmp/money-graph-lane-b-ui.jpg'))
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1050}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='domcontentloaded')
        expect(page.get_by_text('СИНТЕТИЧЕСКИЙ ПРИМЕР · Только проверка интерфейса. Это не анализ предоставленного датасета.', exact=True)).to_be_visible(timeout=30000)
        search = page.get_by_label('Поиск по gid')
        search.fill('9007199254741000')
        search.press('Enter')
        expect(page.get_by_role('heading', name='Клиент 9007199254741000', exact=True)).to_be_visible()
        started = time.monotonic()
        search = page.get_by_label('Поиск по gid')
        search.fill('9007199254741022')
        search.press('Enter')
        expect(page.get_by_role('heading', name='Клиент 9007199254741022', exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_text('Граница обхода depth=4:', exact=False)).to_be_visible()
        print('Boundary lookup seconds:', round(time.monotonic()-started, 3))
        search.fill('9007199254741023')
        search.press('Enter')
        expect(page.get_by_role('heading', name='Клиент 9007199254741023', exact=True)).to_be_visible()
        search.fill('999')
        search.press('Enter')
        expect(page.get_by_text('Клиент с gid «999» не найден.', exact=True)).to_be_visible()
        search.fill('9007199254741000')
        search.press('Enter')
        expect(page.get_by_role('heading', name='Клиент 9007199254741000', exact=True)).to_be_visible()
        page.screenshot(path=str(args.screenshot), full_page=False, type='jpeg', quality=70)
        assert not errors, errors
        print('Browser smoke: boundary, isolate, unknown gid passed; no page errors.')
        browser.close()


if __name__ == '__main__':
    main()
