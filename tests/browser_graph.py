"""Optional real browser check for offline graph interactions; requires Playwright."""
import argparse
import json
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frontend.view import load_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8503')
    parser.add_argument('--outputs', type=Path, default=Path('outputs'))
    args = parser.parse_args()
    bundle = load_bundle(args.outputs)
    gid = bundle.top.iloc[0].gid
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1600, 'height': 1200})
        errors, external = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: external.append(request.url) if request.url.startswith('http') and not request.url.startswith(args.url) else None)
        page.goto(args.url)
        search = page.get_by_label('Поиск по gid')
        search.fill(gid)
        search.press('Enter')
        expect(page.get_by_role('heading', name=f'Клиент {gid}', exact=True)).to_be_visible(timeout=30000)
        frame = page.frame_locator('iframe')
        node = frame.locator(f'.node[data-id="{gid}"]')
        expect(node).to_be_visible()
        data = json.loads(frame.locator('#graph-data').text_content())
        assert data['selected'] == gid
        assert len(data['nodes']) <= 40
        assert all(isinstance(n['id'], str) for n in data['nodes'])
        initial = node.get_attribute('transform')
        edge = frame.locator(f'.edge[data-source="{gid}"], .edge[data-target="{gid}"]').first
        before_edge = edge.get_attribute('d')
        node.locator('circle').scroll_into_view_if_needed()
        box = node.locator('circle').bounding_box()
        page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width']/2 + 60, box['y'] + box['height']/2 + 35, steps=10)
        page.mouse.up()
        assert node.get_attribute('transform') != initial, 'Node drag did not move the node'
        assert edge.get_attribute('d') != before_edge, 'Edges did not follow dragged node'
        world = frame.locator('#world')
        transform = world.get_attribute('transform')
        page.mouse.wheel(0, -250)
        expect(world).not_to_have_attribute('transform', transform)
        frame.get_by_role('button', name='Сбросить', exact=True).click()
        expect(node).to_have_attribute('transform', initial)
        frame.get_by_role('button', name='Свободно', exact=True).click()
        expect(frame.locator('#free')).to_have_class('active')
        frame.get_by_role('button', name='По потокам', exact=True).click()
        frame.get_by_label('Только связи выбранного').check()
        assert frame.locator('.edge').evaluate_all('(edges)=>edges.every(e=>e.classList.contains("active") || e.style.display==="none")')
        frame.get_by_label('Подписи', exact=True).check()
        assert frame.locator('.node text').evaluate_all('(labels)=>labels.every(e=>e.style.display!=="none")')
        page.screenshot(path='/tmp/money-graph-interactive.jpg', full_page=True)
        page.get_by_text('Сеть и сообщества', exact=True).click()
        expect(page.get_by_role('heading', name='Как устроена сеть', exact=True)).to_be_visible()
        overview = page.frame_locator('iframe')
        expect(overview.locator('.node').first).to_be_visible()
        expect(overview.locator('.node')).to_have_count(min(100, len(bundle.clusters)))
        overview_data = json.loads(overview.locator('#graph-data').text_content())
        xs = [node['x'] for node in overview_data['nodes']]
        ys = [node['y'] for node in overview_data['nodes']]
        assert .4 < (max(xs)-min(xs))/max(1, max(ys)-min(ys)) < 2.5, 'Overview became a distant row again'
        page.screenshot(path='/tmp/money-graph-overview.jpg', full_page=True)
        assert not errors, errors
        assert not external, external
        print('PASS: real graph drag, edge tracking, zoom, reset, layouts, labels, focus, overview; no JS errors or external requests')
        browser.close()


if __name__ == '__main__':
    main()
