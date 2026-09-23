"""Deterministic role strengths, independent review priority, factual evidence."""
from collections import defaultdict, deque

import networkx as nx
import numpy as np
import pandas as pd

SEED = 42
ROLES = ("consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral")
PRIORITY_WEIGHTS = {"structure": 0.30, "seed": 0.20, "magnitude": 0.25,
                    "role": 0.15, "temporal": 0.10}
ROLE_SIGNIFICANCE = {"coordinator": 1.0, "consolidator": 0.9, "distributor": 0.9,
                     "transit": 0.8, "terminal": 0.6, "peripheral": 0.1}


def normalize(values: pd.Series) -> pd.Series:
    """log1p / positive-value 95th percentile, clipped; zero stays zero."""
    logged = np.log1p(values.clip(lower=0).astype(float))
    positive = logged[logged > 0]
    scale = positive.quantile(0.95) if len(positive) else 0.0
    return (logged / scale).clip(0, 1) if scale > 0 else logged * 0


def rapid_flow(transactions: pd.DataFrame) -> dict:
    """FIFO amount matching without reusing inflows; timestamps within 48h.

    This is temporal compatibility, not attribution of outgoing money to incoming money.
    Equal timestamps count as compatible (date-only exports cannot establish order).
    """
    events = defaultdict(list)
    for row in transactions.itertuples(index=False):
        events[row.dst].append((row.date, 0, float(row.sum_kzt)))
        events[row.src].append((row.date, 1, float(row.sum_kzt)))
    result = {}
    for gid, rows in events.items():
        queue = deque()
        matched = 0.0
        for date, direction, amount in sorted(rows):
            while queue and date - queue[0][0] > pd.Timedelta(hours=48):
                queue.popleft()
            if direction == 0:
                queue.append([date, amount])
            else:
                remaining = amount
                while queue and remaining > 0:
                    take = min(queue[0][1], remaining)
                    matched += take
                    remaining -= take
                    queue[0][1] -= take
                    if queue[0][1] <= 0:
                        queue.popleft()
        result[gid] = matched
    return result


def build_features(nodes, edges, transactions):
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.gid)
    for e in edges.itertuples(index=False):
        graph.add_edge(e.src, e.dst, weight=float(e.sum_kzt), n_tx=int(e.n_tx))
    projection = nx.Graph()
    projection.add_nodes_from(graph)
    for u, v, d in graph.edges(data=True):
        previous = projection.get_edge_data(u, v, {}).get("weight", 0)
        projection.add_edge(u, v, weight=previous + d["weight"])
    if projection.number_of_edges():
        communities = nx.community.louvain_communities(projection, weight="weight", seed=SEED)
    else:
        communities = [{gid} for gid in graph]
    communities = sorted(communities, key=lambda c: (-len(c), min(c)))
    membership = {gid: i for i, group in enumerate(communities) for gid in group}
    f = nodes.set_index("gid").copy()
    f["cluster_id"] = pd.Series(membership)
    f["in_degree"] = pd.Series(dict(graph.in_degree()))
    f["out_degree"] = pd.Series(dict(graph.out_degree()))
    for name, endpoint, value in (("incoming_sum", "dst", "sum_kzt"),
                                   ("outgoing_sum", "src", "sum_kzt"),
                                   ("incoming_n_tx", "dst", "n_tx"),
                                   ("outgoing_n_tx", "src", "n_tx")):
        f[name] = edges.groupby(endpoint)[value].sum().reindex(f.index, fill_value=0)
    f["ratio_observed"] = f.incoming_sum.gt(0)
    f["pass_through_ratio"] = f.outgoing_sum / f.incoming_sum.where(f.ratio_observed, 1)
    f.loc[~f.ratio_observed, "pass_through_ratio"] = 0.0
    f["ratio_reliable"] = f.ratio_observed & ~f.is_seed & f.depth.lt(4)
    f["truncated_by_depth"] = f.depth.ge(4)
    f["pagerank"] = pd.Series(nx.pagerank(graph, weight="weight", max_iter=1000, tol=1e-10))
    # Unweighted directed shortest paths: currency amount is not path distance.
    f["betweenness"] = pd.Series(nx.betweenness_centrality(
        graph, k=min(128, len(graph)), seed=SEED, weight=None, normalized=True))
    seeds = list(f.index[f.is_seed])
    distances = nx.multi_source_dijkstra_path_length(graph, seeds, weight=None) if seeds else {}
    f["seed_distance"] = pd.Series(distances).reindex(f.index, fill_value=-1).astype(int)
    f["seed_proximity"] = f.seed_distance.map(lambda d: 1 / (1 + d) if d >= 0 else 0)
    neighbors = {g: set(graph.predecessors(g)) | set(graph.successors(g)) for g in graph}
    f["cross_cluster_degree"] = pd.Series({
        g: sum(membership[n] != membership[g] for n in adjacent)
        for g, adjacent in neighbors.items()})
    f["bridge_fraction"] = f.cross_cluster_degree / pd.Series(
        {g: max(1, len(adjacent)) for g, adjacent in neighbors.items()})
    f["rapid_matched_kzt"] = pd.Series(rapid_flow(transactions)).reindex(f.index, fill_value=0)
    # Denominator is observed incoming, preventing a tiny outgoing amount from scoring 1.
    f["rapid_fraction"] = (f.rapid_matched_kzt / f.incoming_sum.where(f.ratio_observed, 1)).clip(0, 1)
    return f, graph


def score_roles(f):
    f = f.copy()
    ni, no = normalize(f.in_degree), normalize(f.out_degree)
    vi, vo = normalize(f.incoming_sum), normalize(f.outgoing_sum)
    b, p = normalize(f.betweenness), normalize(f.pagerank)
    bridge = f.bridge_fraction * normalize(f.cross_cluster_degree)
    retained = (1 - f.pass_through_ratio.clip(0, 1)).where(f.ratio_reliable, 0)
    balance = np.exp(-np.abs(np.log(f.pass_through_ratio.clip(lower=1e-12))))
    balance = balance.where(f.ratio_reliable, 0)
    both = f.in_degree.gt(0) & f.out_degree.gt(0)
    f["score_consolidator"] = (0.45 * ni + 0.30 * vi + 0.25 * retained) * f.in_degree.ge(2)
    fanout = f.out_degree / (f.in_degree + f.out_degree).clip(lower=1)
    f["score_distributor"] = (0.45 * no + 0.30 * vo + 0.25 * fanout) * f.out_degree.ge(2)
    f["score_transit"] = (0.50 * balance + 0.35 * f.rapid_fraction +
                          0.15 * np.minimum(vi, vo)) * both
    # Missing seed inflows cannot support a strong balance-based transit claim.
    f.loc[f.is_seed, "score_transit"] *= 0.55
    f["score_terminal"] = (0.65 * vi + 0.35 * ni) * f.in_degree.gt(0) * f.out_degree.eq(0)
    f.loc[f.truncated_by_depth, "score_terminal"] = 0.0
    f["score_coordinator"] = (0.60 * b + 0.20 * p + 0.20 * bridge) * both
    substantive = [f"score_{r}" for r in ROLES[:-1]]
    # Boundary nodes retain observable fan-in/fan-out evidence with reduced strength.
    f.loc[f.truncated_by_depth, substantive] *= 0.75
    strongest = f[substantive].max(axis=1)
    f["score_peripheral"] = np.where(strongest < 0.45, 1 - strongest, 0.0)
    score_columns = [f"score_{r}" for r in ROLES]
    f["role"] = f[score_columns].idxmax(axis=1).str.removeprefix("score_")
    f["role_score"] = f[score_columns].max(axis=1)
    f["priority_structure"] = 0.60 * b + 0.25 * p + 0.15 * bridge
    f["priority_seed"] = f.seed_proximity
    f["priority_magnitude"] = normalize(f.incoming_sum + f.outgoing_sum)
    f["priority_role"] = f.role.map(ROLE_SIGNIFICANCE) * f.role_score
    f["priority_temporal"] = f.rapid_fraction * (0.5 + 0.5 * np.minimum(vi, vo))
    f["priority_score"] = sum(weight * f[f"priority_{key}"]
                              for key, weight in PRIORITY_WEIGHTS.items()).clip(0, 1)
    f["evidence"] = [evidence(row) for row in f.itertuples()]
    return f


def money(value):
    if value >= 1e9:
        return f"{value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{value / 1e6:.2f}M"
    if value >= 1e3:
        return f"{value / 1e3:.1f}K"
    return f"{value:.0f}"


def evidence(r):
    texts = {
        "consolidator": f"Получает от {r.in_degree} контрагентов, вход {money(r.incoming_sum)} KZT; выход {money(r.outgoing_sum)} KZT.",
        "distributor": f"Отправляет {r.out_degree} контрагентам, выход {money(r.outgoing_sum)} KZT; входящих контрагентов {r.in_degree}.",
        "transit": f"Вход/выход {money(r.incoming_sum)}/{money(r.outgoing_sum)} KZT; совместимо с выводом ≤48ч: {r.rapid_fraction:.0%} входа.",
        "terminal": f"Вход {money(r.incoming_sum)} KZT от {r.in_degree} контрагентов; исходящих нет в выборке; depth={r.depth}.",
        "coordinator": f"Посредничество {r.betweenness:.4f}; PageRank {r.pagerank:.4f}; {r.cross_cluster_degree} связей с другими кластерами.",
        "peripheral": f"Контрагентов: вход {r.in_degree}, выход {r.out_degree}; потоки {money(r.incoming_sum)}/{money(r.outgoing_sum)} KZT; сильной роли нет.",
    }
    suffix = " Граница depth=4: продолжение потока неизвестно." if r.truncated_by_depth else ""
    if r.is_seed:
        suffix += " Seed: вход неполон."
    return (texts[r.role] + suffix)[:200]


def make_outputs(f, edges, top_n=20):
    ranked = f.reset_index().sort_values(["priority_score", "gid"], ascending=[False, True])
    nodes_roles = f.reset_index()[["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]]
    top = ranked.head(max(20, top_n))[["gid", "role", "priority_score", "evidence"]].copy()
    top.insert(0, "rank", range(1, len(top) + 1))
    reasons = []
    for row in ranked.head(max(20, top_n)).itertuples():
        parts = sorted(((key, weight * getattr(row, f"priority_{key}"))
                        for key, weight in PRIORITY_WEIGHTS.items()), key=lambda x: -x[1])[:2]
        labels = {"structure": "структура", "seed": "близость seed", "magnitude": "объём",
                  "role": "роль", "temporal": "время"}
        reasons.append(row.evidence + " Приоритет: " + "; ".join(
            f"{labels[k]} +{v:.3f}" for k, v in parts) + ".")
    top = top.drop(columns="evidence")
    top["why"] = reasons
    internal = edges.assign(src_cluster=edges.src.map(f.cluster_id), dst_cluster=edges.dst.map(f.cluster_id))
    internal_sums = internal.loc[internal.src_cluster.eq(internal.dst_cluster)].groupby("src_cluster").sum_kzt.sum()
    clusters = []
    for cid, group in f.groupby("cluster_id", sort=True):
        important = ranked.loc[ranked.cluster_id.eq(cid)].head(5).gid
        roles = group.role.value_counts()
        dominant = sorted(roles.index, key=lambda role: (-roles[role], role))[0]
        clusters.append({"cluster_id": int(cid), "n_nodes": len(group), "n_seed": int(group.is_seed.sum()),
                         "sum_kzt_internal": float(internal_sums.get(cid, 0)),
                         "top_gids": "|".join(important),
                         "hypothesis": f"Сообщество потоков: {len(group)} узлов; чаще {dominant} ({roles[dominant]}); "
                                       f"на границе depth=4: {int(group.truncated_by_depth.sum())}. Графовая гипотеза."})
    return nodes_roles, pd.DataFrame(clusters), top
