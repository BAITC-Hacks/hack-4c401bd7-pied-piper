"""Contract-shaped, factual exports for the reviewed graph."""
import pandas as pd

from src.contracts import (
    CLUSTERS_SCHEMA, NODE_METRICS_SCHEMA, NODES_ROLES_SCHEMA,
    PRIORITY_WEIGHTS, TOP_NODES_SCHEMA, column_names,
)


def _evidence(row) -> str:
    facts = {
        "consolidator": (f"Вход от {row.in_deg} контр.: {row.in_kzt:.2f} KZT; "
                         f"выход {row.out_kzt:.2f} KZT."),
        "distributor": (f"Выход {row.out_deg} контр.: {row.out_kzt:.2f} KZT; "
                        f"вход {row.in_kzt:.2f} KZT."),
        "transit": (f"Вход/выход: {row.in_kzt:.2f}/{row.out_kzt:.2f} KZT; "
                    f"отношение ≈{row.pass_through:.2f}."),
        "terminal": (f"Вход {row.in_kzt:.2f} KZT от {row.in_deg} контр.; "
                     f"наблюдаемых исходящих связей {row.out_deg}."),
        "coordinator": (f"Прибл. посредничество {row.betweenness:.5f}; "
                        f"внешних соседей {row.cross_cluster_degree}; "
                        f"вход/выход {row.in_deg}/{row.out_deg} контр."),
        "peripheral": (f"Вход/выход {row.in_deg}/{row.out_deg} контр.; "
                       f"потоки {row.in_kzt:.2f}/{row.out_kzt:.2f} KZT."),
    }[row.role]
    if row.at_boundary:
        caveat = " Depth=4: продолжение неизвестно."
    elif row.is_seed:
        caveat = " Seed: вход неполон."
    elif row.in_kzt == 0:
        caveat = " Вход не наблюдался; отношение не определено."
    else:
        caveat = " Только наблюдаемые переводы ≥5000 KZT."
    return facts + caveat


def make_outputs(features: pd.DataFrame, edges: pd.DataFrame, top_n: int = 20):
    """Return node roles, clusters, Top and typed node metrics."""
    metrics = features.reset_index().copy()
    metrics["evidence"] = [_evidence(row) for row in metrics.itertuples(index=False)]
    for field in NODE_METRICS_SCHEMA:
        if field.dtype.startswith("int"):
            metrics[field.name] = metrics[field.name].astype(
                "Int64" if field.nullable else field.dtype
            )
        elif field.dtype == "float64":
            metrics[field.name] = metrics[field.name].astype("float64")
        elif field.dtype == "bool":
            metrics[field.name] = metrics[field.name].astype(bool)
    metrics = metrics[list(column_names(NODE_METRICS_SCHEMA))]
    roles = metrics[list(column_names(NODES_ROLES_SCHEMA))].copy()
    ranked = metrics.sort_values(["priority_score", "gid"], ascending=[False, True])
    top = ranked.head(max(20, top_n)).copy()
    labels = {
        "structure": "структура", "seed_proximity": "близость seed",
        "magnitude": "объём", "role_support": "поддержка роли",
    }
    why = []
    for row in top.itertuples(index=False):
        contributions = sorted(
            ((key, getattr(row, f"contribution_{key}")) for key in PRIORITY_WEIGHTS),
            key=lambda pair: -pair[1],
        )[:2]
        why.append(row.evidence + " Приоритет: " + "; ".join(
            f"{labels[key]} +{value:.3f}" for key, value in contributions
        ) + ".")
    top["why"] = why
    top.insert(0, "rank", range(1, len(top) + 1))
    top = top[list(column_names(TOP_NODES_SCHEMA))]
    membership = metrics.set_index("gid").cluster_id
    internal = edges.assign(
        src_cluster=edges.src.map(membership),
        dst_cluster=edges.dst.map(membership),
    )
    internal = internal.loc[internal.src_cluster.eq(internal.dst_cluster)]
    sums = internal.groupby("src_cluster").sum_kzt.sum()
    clusters = []
    for cluster_id, group in metrics.groupby("cluster_id", sort=True):
        leaders = ranked.loc[ranked.cluster_id.eq(cluster_id)].head(5).gid
        clusters.append({
            "cluster_id": int(cluster_id),
            "n_nodes": int(len(group)),
            "n_seed": int(group.is_seed.sum()),
            "sum_kzt_internal": float(sums.get(cluster_id, 0.0)),
            "top_gids": "|".join(str(gid) for gid in leaders),
            "hypothesis": (f"Наблюдаемое сообщество из {len(group)} клиентов; "
                           f"{int(group.is_seed.sum())} seed, "
                           f"{int(group.at_boundary.sum())} узлов на границе depth=4."),
        })
    clusters = pd.DataFrame(clusters)[list(column_names(CLUSTERS_SCHEMA))]
    return roles, clusters, top, metrics
