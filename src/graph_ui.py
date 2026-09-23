"""Offline SVG graph payloads; dragging changes display positions only."""
import json
import math
from pathlib import Path

import networkx as nx

from src.view import COLORS, LABELS, overview_graph

PALETTE = ['#679eff', '#f7b955', '#49c9bd', '#ce8ff0', '#ff7f8a', '#96a7be', '#c2d66b']


def graph_html(graph, attributes, color_by='role', selected=None):
    # Lay out each component separately, retaining isolates and avoiding coincident centres.
    positions = {}
    components = sorted(nx.connected_components(graph.to_undirected()),
                        key=lambda c: (-len(c), min(map(str, c))))
    offset_x = offset_y = row_height = 0
    shelf_width = max(900, math.sqrt(max(1, len(graph))) * 150)
    for component in components:
        sub = graph.subgraph(sorted(component))
        layout = nx.spring_layout(sub, seed=42, weight=None, iterations=100, k=.8)
        scale = math.sqrt(len(sub)) * 65 if len(sub) > 1 else 0
        width = scale * 2 + 100
        if offset_x and offset_x + width > shelf_width:
            offset_x = 0
            offset_y += row_height
            row_height = 0
        for node, pos in layout.items():
            positions[node] = [float(pos[0] * scale + offset_x + scale), float(pos[1] * scale + offset_y + scale)]
        offset_x += width
        row_height = max(row_height, width)
    edges = sorted(graph.edges(data=True), key=lambda e: (-e[2].get('sum_kzt', 0), str(e[0]), str(e[1])))[:350]
    nodes = []
    for node in graph:
        attrs = attributes[node]
        category = str(attrs.get(color_by, 'peripheral'))
        color = COLORS.get(category, PALETTE[int(category) % len(PALETTE)] if category.isdigit() else '#96a7be')
        is_cluster = selected is None
        nodes.append(dict(id=str(node), x=positions[node][0], y=positions[node][1], color=color,
                          radius=12 if node == selected else 7 + 4 * float(attrs.get('priority_score', 0)),
                          label=f'К{node}' if is_cluster else '…' + str(node)[-6:],
                          group=LABELS.get(category, f'Кластер {category}'),
                          flow=('both' if graph.has_edge(node, selected) and graph.has_edge(selected, node)
                                else 'in' if graph.has_edge(node, selected)
                                else 'out' if graph.has_edge(selected, node) else 'other'),
                          description=attrs.get('label', LABELS.get(attrs.get('role'), '')),
                          priority=float(attrs.get('priority_score', 0))))
    payload = dict(nodes=nodes, edges=[dict(source=str(a), target=str(b), amount=float(d['sum_kzt']),
                                           count=int(d['n_tx'])) for a, b, d in edges], selected=selected)
    # IDs stay strings (>2**53); escape script delimiters even for future free-text labels.
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return Path(__file__).with_name('graph.html').read_text(encoding='utf-8').replace('__GRAPH_DATA__', encoded)


def ego_html(graph, nodes, gid, color_by='role'):
    return graph_html(graph, nodes.set_index('gid').to_dict('index'), color_by, gid)


def overview_html(bundle, visible_clusters=None, limit=100):
    graph, attrs, hidden = overview_graph(bundle, visible_clusters, limit)
    return graph_html(graph, attrs, 'cluster_id'), hidden, max(0, graph.number_of_edges() - 350)
