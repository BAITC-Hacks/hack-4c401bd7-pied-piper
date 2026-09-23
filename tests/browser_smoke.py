"""Optional browser acceptance against a published bundle (requires Playwright)."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.view import LABELS, load_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8501')
    parser.add_argument('--outputs', type=Path, default=Path('demo_outputs'))
    parser.add_argument('--screenshot', type=Path, default=Path('/tmp/money-graph-lane-b-ui.jpg'))
    args = parser.parse_args()
    bundle = load_bundle(args.outputs)
    nodes = bundle.nodes
    boundary = nodes[nodes.depth.eq(4)]
    isolates = nodes[nodes.in_deg.eq(0) & nodes.out_deg.eq(0) & nodes.is_seed]
    assert not boundary.empty and not isolates.empty, 'QA needs a boundary and isolated seed'
    gids = list(nodes.sample(min(3, len(nodes)), random_state=17).gid)
    gids += [bundle.top.iloc[0].gid, boundary.iloc[0].gid, isolates.iloc[0].gid]
    unknown = str(max(nodes.gid.map(int)) + 1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1050}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='domcontentloaded')
        search = page.get_by_label('Поиск по gid')
        expect(search).to_be_visible(timeout=30000)
        synthetic = page.get_by_text('СИНТЕТИЧЕСКИЙ ПРИМЕР · Только проверка интерфейса. Это не анализ предоставленного датасета.', exact=True)
        if bundle.manifest.get('data_kind') == 'synthetic':
            expect(synthetic).to_be_visible()
        else:
            expect(synthetic).to_have_count(0)
        timings = []
        for gid in dict.fromkeys(gids):
            row = nodes.set_index('gid').loc[gid]
            started = time.monotonic()
            search.fill(gid)
            search.press('Enter')
            expect(page.get_by_role('heading', name=f'Клиент {gid}', exact=True)).to_be_visible(timeout=15000)
            card = page
            expect(card.get_by_text(row.evidence, exact=True)).to_be_visible()
            expect(card.locator('strong').filter(has_text=LABELS[row.role])).to_be_visible()
            for label, value in (
                ('Поддержка роли', f'{row.role_score:.3f}'),
                ('Приоритет проверки', f'{row.priority_score * 100:.1f} / 100'),
                ('Входящий поток', f'{row.in_kzt:,.2f} KZT'.replace(',', ' ')),
                ('Исходящий поток', f'{row.out_kzt:,.2f} KZT'.replace(',', ' ')),
            ):
                metric = card.get_by_test_id('stMetric').filter(has=page.get_by_text(label, exact=True))
                expect(metric.get_by_test_id('stMetricValue')).to_have_text(value)
            expect(card.get_by_text(f'Контрагенты: {row.in_deg} входящих / {row.out_deg} исходящих', exact=True)).to_be_visible()
            expect(card.get_by_text(f'Входящие связи · {row.in_deg}', exact=True)).to_be_visible()
            expect(card.get_by_text(f'Исходящие связи · {row.out_deg}', exact=True)).to_be_visible()
            if row.depth == 4:
                expect(card.get_by_text('Граница обхода depth=4:', exact=False)).to_be_visible()
            if row.is_seed:
                expect(card.get_by_text('Seed: входящие потоки неполны.', exact=False)).to_be_visible()
            timings.append(round(time.monotonic() - started, 3))
        search.fill(unknown)
        search.press('Enter')
        expect(page.get_by_text(f'Клиент с gid «{unknown}» не найден.', exact=True)).to_be_visible()
        search.fill(bundle.top.iloc[0].gid)
        search.press('Enter')
        expect(page.get_by_role('heading', name=f'Клиент {bundle.top.iloc[0].gid}', exact=True)).to_be_visible()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(args.screenshot), full_page=True, type='jpeg', quality=70)
        page.get_by_text('Скачать результаты и проверить данные', exact=True).click()
        for label, filename in (
            ('Скачать top_nodes.csv', 'top_nodes.csv'),
            ('Скачать все роли', 'nodes_roles.csv'),
            ('Скачать clusters.csv', 'clusters.csv'),
        ):
            with page.expect_download() as pending:
                page.get_by_role('button', name=label, exact=True).click()
            download = pending.value
            assert download.failure() is None
            assert download.suggested_filename == filename
            assert sha256(Path(download.path()).read_bytes()).hexdigest() == bundle.manifest['output_sha256'][filename], filename
        expect(page.get_by_test_id('stException')).to_have_count(0)
        assert not errors, errors
        print(f'Browser QA passed: run={bundle.manifest["run_id"]}; {len(timings)} cards; boundary; isolated seed; unknown gid; 3 CSV SHA256 matches; no page errors.')
        print(f'Lookup seconds: {timings}; screenshot: {args.screenshot}')
        browser.close()


if __name__ == '__main__':
    main()
