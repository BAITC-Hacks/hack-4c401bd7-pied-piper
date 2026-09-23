"""Deterministic graph features, role support and review priority."""
import math

import networkx as nx
import numpy as np
import pandas as pd

from src.contracts import PRIORITY_WEIGHTS, RANDOM_SEED, ROLES, SUBSTANTIVE_ROLES


SCALE_FIELDS = (
    "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx",
    "betweenness", "pagerank", "cross_cluster_degree", "turnover_kzt",
)


def _scale(values: pd.Series) -> float:
    logged = np.log1p(values.astype(float))
    positive = logged[logged.gt(0)]
    return float(positive.quantile(.95)) if len(positive) else 0.0


def normalize(values: pd.Series) -> pd.Series:
    """Normalize a nonnegative signal by its positive log 95th percentile."""
    logged = np.log1p(values.astype(float).clip(lower=0))
    scale = _scale(values)
    return (logged / scale).clip(0, 1) if scale else logged * 0


def _quantile(values: pd.Series, q: float = .75) -> float | None:
    return float(values.quantile(q)) if len(values) else None


def build_features(nodes, edges, transactions):
    """Calculate directed graph facts while retaining isolated nodes."""
    graph = nx.DiGraph()
    graph.add_nodes_from(sorted(nodes.gid))
    for edge in edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(edge.src, edge.dst, weight=float(edge.sum_kzt), n_tx=int(edge.n_tx))
    components = sorted(nx.weakly_connected_components(graph), key=lambda g: (-len(g), min(g)))
    component_of = {gid: index for index, group in enumerate(components) for gid in group}
    projection = nx.Graph()
    projection.add_nodes_from(graph)
    for src, dst, attributes in graph.edges(data=True):
        current = projection.get_edge_data(src, dst, {}).get("weight", 0.0)
        projection.add_edge(src, dst, weight=current + attributes["weight"])
    communities = []
    for component in components:
        if len(component) == 1:
            communities.append(set(component))
        else:
            subgraph = projection.subgraph(sorted(component))
            communities.extend(nx.community.louvain_communities(
                subgraph, weight="weight", seed=RANDOM_SEED
            ))
    communities.sort(key=lambda g: (-len(g), min(g)))
    cluster_of = {gid: index for index, group in enumerate(communities) for gid in group}

    f = nodes.sort_values("gid").set_index("gid").copy()
    f["component_id"] = pd.Series(component_of, dtype="int64")
    f["cluster_id"] = pd.Series(cluster_of, dtype="int64")
    f["in_deg"] = pd.Series(dict(graph.in_degree()), dtype="int64")
    f["out_deg"] = pd.Series(dict(graph.out_degree()), dtype="int64")
    for name, endpoint, value in (
        ("in_kzt", "dst", "sum_kzt"), ("out_kzt", "src", "sum_kzt"),
        ("in_tx", "dst", "n_tx"), ("out_tx", "src", "n_tx"),
    ):
        f[name] = edges.groupby(endpoint)[value].sum().reindex(f.index, fill_value=0)
    for name in ("in_kzt", "out_kzt"):
        f[name] = f[name].astype("float64")
    for name in ("in_tx", "out_tx"):
        f[name] = f[name].astype("int64")
    f["pass_through"] = f.out_kzt / f.in_kzt.where(f.in_kzt.gt(0))
    f["at_boundary"] = f.depth.eq(4)
    f["truncated_by_depth"] = f.at_boundary & f.out_deg.eq(0)
    f["ratio_usable"] = f.in_kzt.gt(0) & ~f.is_seed & ~f.at_boundary
    f["pagerank"] = pd.Series(nx.pagerank(
        graph, weight="weight", max_iter=1000, tol=1e-10
    ), dtype="float64")
    f["betweenness"] = pd.Series(nx.betweenness_centrality(
        graph, k=min(128, len(graph)), seed=RANDOM_SEED, weight=None,
        normalized=True,
    ), dtype="float64")
    seeds = sorted(f.index[f.is_seed])
    distances = nx.multi_source_dijkstra_path_length(graph, seeds, weight=None) if seeds else {}
    f["seed_distance"] = pd.Series(distances, dtype="Int64").reindex(f.index)
    neighbors = {gid: set(graph.predecessors(gid)) | set(graph.successors(gid)) for gid in graph}
    f["cross_cluster_degree"] = pd.Series({
        gid: sum(cluster_of[neighbor] != cluster_of[gid] for neighbor in adjacent)
        for gid, adjacent in neighbors.items()
    }, dtype="int64")
    f["bridge_fraction"] = pd.Series({
        gid: f.at[gid, "cross_cluster_degree"] / max(1, len(adjacent))
        for gid, adjacent in neighbors.items()
    }, dtype="float64")
    f["bridge"] = f.bridge_fraction * normalize(f.cross_cluster_degree)

    # Retain old feature names for callers of the original analytical draft.
    for old, new in (
        ("in_degree", "in_deg"), ("out_degree", "out_deg"),
        ("incoming_sum", "in_kzt"), ("outgoing_sum", "out_kzt"),
        ("incoming_n_tx", "in_tx"), ("outgoing_n_tx", "out_tx"),
        ("pass_through_ratio", "pass_through"),
    ):
        f[old] = f[new]
    return f, graph


def score_roles(features):
    """Apply data-derived gates, role strengths and independent priority."""
    f = features.copy()
    raw = {key: f.in_kzt + f.out_kzt if key == "turnover_kzt" else f[key]
           for key in SCALE_FIELDS}
    scales = {key: _scale(values) for key, values in raw.items()}
    norm = {key: normalize(values) for key, values in raw.items()}
    in_positive = f.in_deg[f.in_deg.gt(0)]
    out_positive = f.out_deg[f.out_deg.gt(0)]
    in_min = max(2, math.ceil(in_positive.quantile(.75))) if len(in_positive) else None
    out_min = max(2, math.ceil(out_positive.quantile(.75))) if len(out_positive) else None
    terminal_min = _quantile(f.in_kzt[f.in_kzt.gt(0)])
    base_transit = f.in_deg.gt(0) & f.out_deg.gt(0) & f.ratio_usable & f.pass_through.gt(0)
    ratio = f.pass_through.where(base_transit)
    balance = np.minimum(ratio, 1 / ratio)
    transit_min = _quantile(balance[base_transit])
    coordinator_min = _quantile(f.betweenness[f.betweenness.gt(0)])
    thresholds = {
        "in_degree_min": in_min,
        "out_degree_min": out_min,
        "terminal_in_kzt_min": terminal_min,
        "transit_balance_min": transit_min,
        "coordinator_betweenness_min": coordinator_min,
    }
    denominator = (f.in_deg + f.out_deg).clip(lower=1)
    fan_in = f.in_deg / denominator
    fan_out = f.out_deg / denominator
    score = {
        "consolidator": .40 * norm["in_deg"] + .25 * norm["in_kzt"]
                        + .15 * norm["in_tx"] + .20 * fan_in,
        "distributor": .40 * norm["out_deg"] + .25 * norm["out_kzt"]
                       + .15 * norm["out_tx"] + .20 * fan_out,
        "transit": .45 * balance.fillna(0)
                   + .35 * np.minimum(norm["in_kzt"], norm["out_kzt"])
                   + .20 * np.minimum(norm["in_tx"], norm["out_tx"]),
        "terminal": .50 * norm["in_kzt"] + .25 * norm["in_deg"] + .25 * norm["in_tx"],
        "coordinator": .45 * norm["betweenness"] + .20 * norm["pagerank"]
                       + .20 * f.bridge + .15 * np.minimum(norm["in_deg"], norm["out_deg"]),
    }
    eligible = {
        "consolidator": f.in_deg.ge(in_min) if in_min is not None else pd.Series(False, index=f.index),
        "distributor": f.out_deg.ge(out_min) if out_min is not None else pd.Series(False, index=f.index),
        "transit": base_transit & balance.ge(transit_min) if transit_min is not None else pd.Series(False, index=f.index),
        "terminal": (f.depth.lt(4) & f.in_deg.gt(0) & f.out_deg.eq(0) & f.in_kzt.ge(terminal_min))
                    if terminal_min is not None else pd.Series(False, index=f.index),
        "coordinator": (f.in_deg.gt(0) & f.out_deg.gt(0) & f.betweenness.gt(0)
                        & f.betweenness.ge(coordinator_min)
                        & (f.cross_cluster_degree.ge(2) | (f.in_deg.ge(2) & f.out_deg.ge(2))))
                        if coordinator_min is not None else pd.Series(False, index=f.index),
    }
    reasons = {
        "consolidator": "Недостаточно входящих контрагентов.",
        "distributor": "Недостаточно исходящих контрагентов.",
        "transit": "Нет пригодного наблюдаемого баланса входа и выхода.",
        "terminal": "Нет внутреннего наблюдаемого sink с достаточным входом.",
        "coordinator": "Недостаточно структурного посредничества.",
    }
    for role in SUBSTANTIVE_ROLES:
        f[f"eligible_{role}"] = eligible[role].fillna(False).astype(bool)
        f[f"ineligible_reason_{role}"] = pd.Series(
            np.where(f[f"eligible_{role}"], None, reasons[role]), index=f.index, dtype="object")
        f[f"score_{role}"] = score[role].where(f[f"eligible_{role}"], 0).astype(float)

    roles, alternatives, alternative_scores, margins, strengths = [], [], [], [], []
    for row in f.itertuples():
        accepted = [role for role in SUBSTANTIVE_ROLES if getattr(row, f"eligible_{role}")]
        accepted.sort(key=lambda role: -getattr(row, f"score_{role}"))
        chosen = accepted[0] if accepted else "peripheral"
        alternative = accepted[1] if len(accepted) > 1 else None
        first = getattr(row, f"score_{chosen}") if accepted else 1.0
        second = getattr(row, f"score_{alternative}") if alternative else 0.0
        strength = first * (1 - .5 * second)
        if row.at_boundary or row.in_deg + row.out_deg == 0:
            strength = min(.5, strength)
        roles.append(chosen)
        alternatives.append(alternative)
        alternative_scores.append(float(second))
        margins.append(float(first - second if accepted else 0.0))
        strengths.append(float(strength))
    f["score_peripheral"] = [float(role == "peripheral") for role in roles]
    f["role"] = roles
    f["alternative_role"] = alternatives
    f["alternative_score"] = alternative_scores
    f["role_margin"] = margins
    f["role_score"] = strengths
    f["priority_structure"] = .60 * norm["betweenness"] + .25 * norm["pagerank"] + .15 * f.bridge
    f["priority_seed_proximity"] = f.seed_distance.map(
        lambda distance: 1 / (1 + distance) if pd.notna(distance) else 0
    ).astype(float)
    f["priority_magnitude"] = norm["turnover_kzt"]
    f["priority_role_support"] = f.role_score.where(f.role.ne("peripheral"), 0)
    for key, weight in PRIORITY_WEIGHTS.items():
        f[f"contribution_{key}"] = weight * f[f"priority_{key}"]
    f["priority_score"] = f[[f"contribution_{key}" for key in PRIORITY_WEIGHTS]].sum(axis=1)
    f.attrs["thresholds"] = thresholds
    f.attrs["normalization_scales"] = scales
    return f
