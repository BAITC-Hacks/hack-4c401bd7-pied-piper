"""Offline SVG graph payloads; dragging changes display positions only."""
import json
import math
from pathlib import Path

import networkx as nx

from frontend.view import COLORS, LABELS, overview_graph

PALETTE = ['#679eff', '#f7b955', '#49c9bd', '#ce8ff0', '#ff7f8a', '#96a7be', '#c2d66b']


def component_positions(graph):
    """Pack disconnected components in concentric rings, largest at the centre."""
    positions, placed = {}, []
    components = sorted(nx.connected_components(graph.to_undirected()),
                        key=lambda c: (-len(c), min(map(str, c))))
    for index, component in enumerate(components):
        sub = graph.subgraph(sorted(component))
        layout = nx.spring_layout(sub, seed=42, weight=None, iterations=100, k=.8)
        scale = math.sqrt(len(sub)) * 34 if len(sub) > 1 else 0
        radius = max((math.hypot(float(p[0]), float(p[1])) * scale for p in layout.values()), default=0) + 28
        if not placed:
            cx = cy = 0.0
        else:
            # Search the nearest ring with room; golden-angle starts spread small islands.
            ring = placed[0][2] + radius + 22
            while True:
                found = None
                for step in range(180):
                    angle = index * 2.399963229728653 + step * math.tau / 180
                    cx, cy = ring * math.cos(angle), ring * math.sin(angle)
                    if all(math.hypot(cx - x, cy - y) >= radius + r + 18 for x, y, r in placed):
                        found = (cx, cy)
                        break
                if found:
                    cx, cy = found
                    break
                ring += 22
        placed.append((cx, cy, radius))
        for node, pos in layout.items():
            positions[node] = [float(pos[0] * scale + cx), float(pos[1] * scale + cy)]
    return positions


def graph_html(graph, attributes, color_by='role', selected=None):
    positions = component_positions(graph)
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
