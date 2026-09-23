"""Load and validate the three source Parquet tables without losing int64 IDs."""
from pathlib import Path
from datetime import date, datetime

import numpy as np
import pandas as pd

from backend.core.contracts import EXPECTED_NODE_COUNT, INPUT_SCHEMAS, SUM_ATOL_KZT, SUM_RTOL


def _integer(frame: pd.DataFrame, column: str, filename: str, dtype: str) -> None:
    values = frame[column]
    if values.dtype.kind != "i" or values.dtype.itemsize != int(dtype[3:]) // 8:
        raise ValueError(f"{filename}: {column} должен иметь тип {dtype}")


def load_data(directory: Path, expected_nodes: int | None = EXPECTED_NODE_COUNT):
    """Return validated nodes, edges, transactions and input diagnostics."""
    directory = Path(directory)
    tables = {}
    for filename, schema in INPUT_SCHEMAS.items():
        path = directory / filename
        if not path.is_file():
            raise ValueError(f"Нет {path}. Поместите исходные parquet в {directory}.")
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{filename}: не удалось прочитать Parquet: {exc}") from exc
        columns = [field.name for field in schema]
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{filename}: нет колонок {sorted(missing)}")
        frame = frame[columns].copy()
        if frame.isna().any().any():
            raise ValueError(f"{filename}: NULL в обязательных полях")
        for field in schema:
            if field.dtype.startswith("int"):
                _integer(frame, field.name, filename, field.dtype)
        if "sum_kzt" in frame:
            if frame.sum_kzt.dtype != "float64":
                raise ValueError(f"{filename}: sum_kzt должен иметь тип float64")
            try:
                frame["sum_kzt"] = pd.to_numeric(frame.sum_kzt, errors="raise").astype("float64")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{filename}: sum_kzt должен быть числом") from exc
            if not np.isfinite(frame.sum_kzt).all():
                raise ValueError(f"{filename}: sum_kzt содержит NaN/Inf")
            if not frame.sum_kzt.ge(5000).all():
                raise ValueError(f"{filename}: сумма < 5000 KZT")
        tables[filename] = frame

    nodes = tables["nodes.parquet"]
    edges = tables["edges.parquet"]
    tx = tables["transactions.parquet"]
    if nodes.empty or nodes.gid.duplicated().any():
        raise ValueError("nodes: требуется непустой набор уникальных gid")
    if expected_nodes is not None and len(nodes) != expected_nodes:
        raise ValueError(f"Ожидалось {expected_nodes} nodes, получено {len(nodes)}")
    if not nodes.depth.between(0, 4).all():
        raise ValueError("nodes: depth вне 0..4")
    if not pd.api.types.is_bool_dtype(nodes.is_seed):
        raise ValueError("nodes: is_seed должен иметь тип bool")
    if not nodes.is_seed.eq(nodes.depth.eq(0)).all():
        raise ValueError("nodes: seed не согласован с depth=0")
    if not edges.depth.between(1, 4).all():
        raise ValueError("edges: depth вне 1..4")
    if not edges.n_tx.gt(0).all():
        raise ValueError("edges: n_tx должен быть > 0")
    if edges.duplicated(["src", "dst"]).any():
        raise ValueError("edges: повторная пара src/dst")
    ids = set(nodes.gid)
    for name, frame in (("edges", edges), ("transactions", tx)):
        if not (set(frame.src) | set(frame.dst)) <= ids:
            raise ValueError(f"{name}: неизвестные gid в src/dst")
    try:
        if not tx.date.map(lambda value: isinstance(value, date) and not isinstance(value, datetime)).all():
            raise ValueError("transactions: date должен быть календарной датой без времени")
        dates = pd.to_datetime(tx.date, errors="raise", utc=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("transactions: некорректная дата") from exc
    if not dates.eq(dates.dt.normalize()).all() or not dates.between(
        pd.Timestamp("2026-07-01", tz="UTC"), pd.Timestamp("2026-07-31", tz="UTC")
    ).all():
        raise ValueError("transactions: дата должна быть днём июля 2026")
    tx["date"] = dates
    aggregate = tx.groupby(["src", "dst"], as_index=False).agg(
        tx_sum=("sum_kzt", "sum"), tx_count=("sum_kzt", "size")
    )
    check = edges.merge(aggregate, on=["src", "dst"], how="outer", indicator=True)
    if (not check._merge.eq("both").all()
            or not np.allclose(check.sum_kzt, check.tx_sum, rtol=SUM_RTOL, atol=SUM_ATOL_KZT)
            or not check.n_tx.eq(check.tx_count).all()):
        raise ValueError("edges и transactions расходятся по парам, суммам или n_tx")
    diagnostics = {
        "input_nodes": len(nodes), "input_edges": len(edges),
        "input_transactions": len(tx), "n_seed": int(nodes.is_seed.sum()),
        "depth_4_nodes": int(nodes.depth.eq(4).sum()),
        "self_loop_edges": int(edges.src.eq(edges.dst).sum()),
        "self_loop_transactions": int(tx.src.eq(tx.dst).sum()),
    }
    edges["depth"] = edges.depth.astype("int8")
    return (nodes.sort_values("gid").reset_index(drop=True),
            edges.sort_values(["src", "dst"]).reset_index(drop=True),
            tx.sort_values(["date", "src", "dst", "sum_kzt"]).reset_index(drop=True),
            diagnostics)
