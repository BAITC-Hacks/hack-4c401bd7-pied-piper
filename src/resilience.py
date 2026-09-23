"""Read-only node-removal experiments on the complete observed graph."""
from numbers import Integral

import networkx as nx


def compare_removals(nodes, edges, n=5):
    """Compare fixed top-N priority and turnover, with numeric gid tie-breaking.

    Connectivity is weak (directions ignored), amounts retain every directed
    input edge once. Rankings are not recalculated after each removal. Inputs
    are validated bundle tables; this function never modifies or publishes them.
    """
    if isinstance(n, bool) or not isinstance(n, Integral) or n < 1:
        raise ValueError('n must be a positive integer')
    ranked = nodes[['gid', 'priority_score', 'in_kzt', 'out_kzt']].copy()
    ranked['gid'] = ranked.gid.map(lambda value: str(int(value)))
    ranked['numeric_gid'] = ranked.gid.map(int)
    ranked['turnover'] = ranked.in_kzt + ranked.out_kzt
    links = edges[['src', 'dst', 'sum_kzt']].copy()
    for column in ('src', 'dst'):
        links[column] = links[column].map(lambda value: str(int(value)))
    graph = nx.DiGraph()
    graph.add_nodes_from(ranked.gid)
    graph.add_edges_from(links[['src', 'dst']].itertuples(index=False, name=None))
    original_components = list(nx.weakly_connected_components(graph))
    original_isolates = set(nx.isolates(graph))
    total_kzt = float(links.sum_kzt.sum())

    def scenario(removed):
        excluded = set(removed)
        remaining = graph.copy()
        remaining.remove_nodes_from(excluded)
        components = list(nx.weakly_connected_components(remaining))
        largest = max(map(len, components), default=0)
        isolates = set(nx.isolates(remaining))
        affected = links.src.isin(excluded) | links.dst.isin(excluded)
        affected_kzt = float(links.loc[affected, 'sum_kzt'].sum())
        # Count only surviving pairs: deleting a vertex alone is not fragmentation.
        before_sizes = [len(component - excluded) for component in original_components]
        before_pairs = sum(size * (size - 1) // 2 for size in before_sizes)
        after_pairs = sum(len(c) * (len(c) - 1) // 2 for c in components)
        disconnected_pairs = before_pairs - after_pairs
        return {
            'removed_gids': removed,
            'removed_nodes': len(removed),
            'remaining_nodes': len(remaining),
            'components': len(components),
            'largest_component': largest,
            'largest_share': largest / len(remaining) if len(remaining) else 0.0,
            'isolates': len(isolates),
            'new_isolates': len(isolates - original_isolates),
            'affected_edges': int(affected.sum()),
            'affected_kzt': affected_kzt,
            'affected_share': affected_kzt / total_kzt if total_kzt else 0.0,
            'disconnected_pairs': disconnected_pairs,
            'disconnected_pair_share': disconnected_pairs / before_pairs if before_pairs else 0.0,
        }

    result = {'baseline': scenario([])}
    for name, score in (('priority', 'priority_score'), ('turnover', 'turnover')):
        removed = ranked.sort_values([score, 'numeric_gid'], ascending=[False, True]).head(n).gid.tolist()
        result[name] = scenario(removed)
    return result
