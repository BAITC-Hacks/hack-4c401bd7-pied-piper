"""Strict input contract; preserve client identifiers as strings."""
from pathlib import Path

import numpy as np
import pandas as pd


SCHEMAS = {
    "nodes": ["gid", "depth", "is_seed"],
    "edges": ["src", "dst", "sum_kzt", "n_tx", "depth"],
    "transactions": ["src", "dst", "date", "sum_kzt"],
}


def load_data(directory: Path, expected_nodes: int = 2248):
    tables = {}
    for name, columns in SCHEMAS.items():
        path = directory / f"{name}.parquet"
        if not path.is_file():
            raise ValueError(f"Нет {path}. Поместите исходные parquet в {directory}.")
        frame = pd.read_parquet(path)
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{path.name}: нет колонок {sorted(missing)}")
        frame = frame[columns].copy()
        if frame.isna().any().any():
            raise ValueError(f"{path.name}: NULL в обязательных полях")
        for col in ("gid", "src", "dst"):
            if col in frame:
                frame[col] = frame[col].astype(str)
                if frame[col].str.strip().eq("").any():
                    raise ValueError(f"{path.name}: пустой {col}")
        for col in ("depth", "n_tx", "sum_kzt"):
            if col in frame:
                frame[col] = pd.to_numeric(frame[col], errors="raise")
                if not np.isfinite(frame[col]).all():
                    raise ValueError(f"{path.name}: нечисловой/бесконечный {col}")
                if col in ("depth", "n_tx"):
                    if not frame[col].eq(np.floor(frame[col])).all():
                        raise ValueError(f"{path.name}: {col} должен быть целым")
                    frame[col] = frame[col].astype(int)
                if col == "depth" and not frame[col].between(0, 4).all():
                    raise ValueError(f"{path.name}: depth вне 0..4")
                if col == "n_tx" and not frame[col].gt(0).all():
                    raise ValueError(f"{path.name}: n_tx должен быть > 0")
                if col == "sum_kzt" and not frame[col].ge(5000).all():
                    raise ValueError(f"{path.name}: сумма < 5000 KZT")
        tables[name] = frame

    nodes, edges, tx = (tables[k] for k in SCHEMAS)
    if nodes.empty or nodes.gid.duplicated().any():
        raise ValueError("nodes: требуется непустой набор уникальных gid")
    if expected_nodes and len(nodes) != expected_nodes:
        raise ValueError(f"Ожидалось {expected_nodes} nodes, получено {len(nodes)}")
    seed_values = nodes.is_seed.astype(str).str.lower()
    if not seed_values.isin(["true", "false", "0", "1"]).all():
        raise ValueError("nodes: is_seed должен быть bool или 0/1")
    nodes["is_seed"] = seed_values.isin(["true", "1"])
    ids = set(nodes.gid)
    for name, frame in (("edges", edges), ("transactions", tx)):
        if not (set(frame.src) | set(frame.dst)) <= ids:
            raise ValueError(f"{name}: неизвестные gid в src/dst")
    if edges.duplicated(["src", "dst"]).any():
        raise ValueError("edges: повторная пара src/dst; ожидается одно агрегированное ребро")
    tx["date"] = pd.to_datetime(tx.date, errors="raise", utc=True, format="mixed")
    if tx.date.isna().any():
        raise ValueError("transactions: пустая/некорректная дата")
    aggregate = tx.groupby(["src", "dst"], as_index=False).agg(
        tx_sum=("sum_kzt", "sum"), tx_count=("sum_kzt", "size")
    )
    check = edges.merge(aggregate, on=["src", "dst"], how="outer", indicator=True)
    if (not check._merge.eq("both").all()
            or not np.allclose(check.sum_kzt, check.tx_sum, rtol=1e-9, atol=0.01)
            or not check.n_tx.eq(check.tx_count).all()):
        raise ValueError("edges и transactions расходятся по парам, суммам или n_tx")
    diagnostics = {
        "input_nodes": len(nodes), "input_edges": len(edges),
        "input_transactions": len(tx), "n_seed": int(nodes.is_seed.sum()),
        "depth_4_nodes": int(nodes.depth.eq(4).sum()),
        "excluded_self_loop_edges": int(edges.src.eq(edges.dst).sum()),
        "excluded_self_loop_transactions": int(tx.src.eq(tx.dst).sum()),
    }
    # Own-account loops are not transfers between counterparties.
    edges = edges.loc[edges.src.ne(edges.dst)].sort_values(["src", "dst"])
    tx = tx.loc[tx.src.ne(tx.dst)].sort_values(["date", "src", "dst", "sum_kzt"])
    return nodes.sort_values("gid").reset_index(drop=True), edges, tx, diagnostics
