import json
import re

import networkx as nx

from src.graph_ui import ego_html, graph_html, overview_html
from src.view import load_bundle, make_graph, select_ego


def payload(html):
    return json.loads(re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_interactive_graph_preserves_ids_directions_and_isolates(sample):
    out, _, gids = sample
    bundle = load_bundle(out)
    graph = make_graph(bundle.nodes, bundle.edges)
    ego, _ = select_ego(graph, gids[2], bundle.nodes, hops=1)
    data = payload(ego_html(ego, bundle.nodes, gids[2]))
    assert {n['id'] for n in data['nodes']} == set(ego)
    assert {(e['source'], e['target']) for e in data['edges']} == set(ego.edges)
    assert all(isinstance(n['id'], str) for n in data['nodes'])
    assert {n['id']: n['flow'] for n in data['nodes']}[gids[1]] == 'in'
    isolated, _ = select_ego(graph, gids[-1], bundle.nodes)
    data = payload(ego_html(isolated, bundle.nodes, gids[-1]))
    assert data['nodes'][0]['id'] == gids[-1] and data['edges'] == []
    html, hidden, _ = overview_html(bundle)
    assert len(payload(html)['nodes']) == len(bundle.clusters) and hidden == 0


def test_graph_labels_cannot_break_out_of_script():
    graph = nx.DiGraph()
    graph.add_node('9007199254741001')
    hostile = '</script><script>alert(1)</script>'
    html = graph_html(graph, {'9007199254741001': {'label': hostile}})
    assert hostile not in html
    assert payload(html)['nodes'][0]['description'] == hostile
