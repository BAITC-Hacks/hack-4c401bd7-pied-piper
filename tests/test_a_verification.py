"""Small analytical checks independent of the UI's synthetic bundle."""

import pandas as pd
import pytest

from src.data import load_data
from src.engine import build_features, normalize


BASE_ID = 10000000000000001


def graph_tables():
    """A directed chain, a separate seed and an isolated seed."""
    ids = [BASE_ID + offset for offset in range(5)]
    nodes = pd.DataFrame({
        "gid": ids,
        "depth": [0, 1, 2, 0, 0],
        "is_seed": [True, False, False, True, True],
    })
    edges = pd.DataFrame({
        "src": [ids[0], ids[1], ids[3]],
        "dst": [ids[1], ids[2], ids[2]],
        "sum_kzt": [15000.0, 9000.0, 5000.0],
        "n_tx": [2, 1, 1],
        "depth": [1, 2, 1],
    })
    transactions = pd.DataFrame({
        "src": [ids[0], ids[0], ids[1], ids[3]],
        "dst": [ids[1], ids[1], ids[2], ids[2]],
        "date": pd.to_datetime(["2026-07-01"] * 4, utc=True),
        "sum_kzt": [7500.0, 7500.0, 9000.0, 5000.0],
    })
    return ids, nodes, edges, transactions


def write_tables(directory, nodes, edges, transactions):
    directory.mkdir()
    nodes.to_parquet(directory / "nodes.parquet", index=False)
    edges.to_parquet(directory / "edges.parquet", index=False)
    transactions.to_parquet(directory / "transactions.parquet", index=False)


def test_directed_features_keep_isolate_and_separate_amount_from_count():
    ids, nodes, edges, transactions = graph_tables()
    features, graph = build_features(nodes, edges, transactions)

    assert len(graph) == len(nodes)
    assert graph.has_edge(ids[0], ids[1])
    assert not graph.has_edge(ids[1], ids[0])
    assert features.loc[ids[4], "in_degree"] == 0
    assert features.loc[ids[4], "out_degree"] == 0
    assert features.loc[ids[0], "outgoing_sum"] == 15000
    assert features.loc[ids[0], "outgoing_n_tx"] == 2
    assert features.loc[ids[1], "in_degree"] == 1
    assert features.loc[ids[1], "incoming_n_tx"] == 2
    assert features.loc[ids[2], "in_degree"] == 2


def test_feature_calculation_is_repeatable():
    _, nodes, edges, transactions = graph_tables()
    first, _ = build_features(nodes, edges, transactions)
    second, _ = build_features(nodes, edges, transactions)

    pd.testing.assert_frame_equal(first, second)


def test_normalize_zero_and_positive_values():
    result = normalize(pd.Series([0.0, 0.0, 5.0, 100.0]))
    assert result.iloc[:2].eq(0).all()
    assert result.between(0, 1).all()
    assert result.iloc[3] > result.iloc[2]
    assert normalize(pd.Series([0.0, 0.0])).eq(0).all()


def test_loader_preserves_duplicate_transactions_and_checks_aggregates(tmp_path):
    _, nodes, edges, transactions = graph_tables()
    data = tmp_path / "valid"
    write_tables(data, nodes, edges, transactions)

    loaded_nodes, loaded_edges, loaded_transactions, diagnostics = load_data(
        data, expected_nodes=len(nodes))
    assert len(loaded_nodes) == 5
    assert len(loaded_edges) == 3
    assert len(loaded_transactions) == 4
    assert diagnostics["input_transactions"] == 4

    bad = tmp_path / "bad"
    changed = edges.copy()
    changed.loc[0, "n_tx"] = 1
    write_tables(bad, nodes, changed, transactions)
    with pytest.raises(ValueError, match="расходятся"):
        load_data(bad, expected_nodes=len(nodes))


@pytest.mark.xfail(strict=True, reason="src/data.py still casts signed int64 IDs to strings")
def test_loader_keeps_int64_ids_without_float_conversion(tmp_path):
    _, nodes, edges, transactions = graph_tables()
    data = tmp_path / "ids"
    write_tables(data, nodes, edges, transactions)
    loaded_nodes, loaded_edges, _, _ = load_data(data, expected_nodes=len(nodes))

    assert pd.api.types.is_integer_dtype(loaded_nodes.gid.dtype)
    assert pd.api.types.is_integer_dtype(loaded_edges.src.dtype)
    assert set(loaded_nodes.gid) == set(nodes.gid)


@pytest.mark.xfail(strict=True, reason="src/engine.py still stores undefined pass-through as zero")
def test_zero_inflow_has_undefined_pass_through():
    ids, nodes, edges, transactions = graph_tables()
    features, _ = build_features(nodes, edges, transactions)

    assert pd.isna(features.loc[ids[0], "pass_through_ratio"])
    assert pd.isna(features.loc[ids[4], "pass_through_ratio"])
