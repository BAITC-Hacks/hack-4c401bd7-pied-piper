"""Small analytical checks independent of the UI's synthetic bundle."""

import pandas as pd
import pytest
from subprocess import CompletedProcess

from src.data import load_data
from src.engine import build_features, normalize, score_roles
from src.contracts import PRIORITY_WEIGHTS
from pipeline import run_pipeline
from src.view import load_bundle


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
    edges["depth"] = edges.depth.astype("int8")
    transactions = pd.DataFrame({
        "src": [ids[0], ids[0], ids[1], ids[3]],
        "dst": [ids[1], ids[1], ids[2], ids[2]],
        "date": [pd.Timestamp("2026-07-01").date()] * 4,
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


def test_loader_keeps_int64_ids_without_float_conversion(tmp_path):
    _, nodes, edges, transactions = graph_tables()
    data = tmp_path / "ids"
    write_tables(data, nodes, edges, transactions)
    loaded_nodes, loaded_edges, _, _ = load_data(data, expected_nodes=len(nodes))

    assert pd.api.types.is_integer_dtype(loaded_nodes.gid.dtype)
    assert pd.api.types.is_integer_dtype(loaded_edges.src.dtype)
    assert set(loaded_nodes.gid) == set(nodes.gid)


@pytest.mark.parametrize(("change", "message"), [
    (lambda n, e, t: n.__setitem__("depth", [1, 1, 2, 0, 0]), "seed"),
    (lambda n, e, t: e.__setitem__("depth", [0, 2, 1]), "depth"),
    (lambda n, e, t: t.__setitem__("date", ["2026-08-01"] * len(t)), "дата"),
    (lambda n, e, t: e.__setitem__("dst", [42, e.dst.iloc[1], e.dst.iloc[2]]), "неизвестные"),
    (lambda n, e, t: t.__setitem__("sum_kzt", [float("inf")] + t.sum_kzt.iloc[1:].tolist()), "NaN/Inf"),
])
def test_loader_rejects_invalid_inputs(tmp_path, change, message):
    _, nodes, edges, transactions = graph_tables()
    change(nodes, edges, transactions)
    data = tmp_path / "invalid"
    write_tables(data, nodes, edges, transactions)
    with pytest.raises(ValueError, match=message):
        load_data(data, expected_nodes=len(nodes))


def test_loader_keeps_self_loops(tmp_path):
    ids, nodes, edges, transactions = graph_tables()
    edges = pd.concat([edges, pd.DataFrame({
        "src": [ids[1]], "dst": [ids[1]], "sum_kzt": [5000.0],
        "n_tx": [1], "depth": [2],
    })], ignore_index=True)
    edges["depth"] = edges.depth.astype("int8")
    transactions = pd.concat([transactions, pd.DataFrame({
        "src": [ids[1]], "dst": [ids[1]],
        "date": [pd.Timestamp("2026-07-01").date()], "sum_kzt": [5000.0],
    })], ignore_index=True)
    data = tmp_path / "loops"
    write_tables(data, nodes, edges, transactions)
    _, loaded_edges, loaded_transactions, diagnostics = load_data(data, expected_nodes=len(nodes))
    assert len(loaded_edges) == len(edges)
    assert len(loaded_transactions) == len(transactions)
    assert diagnostics["self_loop_edges"] == 1


def test_zero_inflow_has_undefined_pass_through():
    ids, nodes, edges, transactions = graph_tables()
    features, _ = build_features(nodes, edges, transactions)

    assert pd.isna(features.loc[ids[0], "pass_through_ratio"])
    assert pd.isna(features.loc[ids[4], "pass_through_ratio"])


def test_role_gates_and_priority_arithmetic():
    ids, nodes, edges, transactions = graph_tables()
    nodes.loc[nodes.gid.eq(ids[2]), "depth"] = 4
    scored = score_roles(build_features(nodes, edges, transactions)[0])

    assert not scored.loc[ids[0], "eligible_transit"]
    assert not scored.loc[ids[2], "eligible_terminal"]
    assert scored.loc[ids[2], "role_score"] <= 0.5
    assert scored.loc[ids[4], "role"] == "peripheral"
    assert scored.loc[ids[4], "role_score"] == 0.5
    assert scored.loc[ids[1], "eligible_transit"]
    assert scored.loc[ids[1], "score_transit"] > 0
    for key, weight in PRIORITY_WEIGHTS.items():
        pd.testing.assert_series_equal(
            scored[f"contribution_{key}"],
            scored[f"priority_{key}"] * weight,
            check_names=False,
        )
    pd.testing.assert_series_equal(
        scored.priority_score,
        scored[[f"contribution_{key}" for key in PRIORITY_WEIGHTS]].sum(axis=1),
        check_names=False,
    )


def test_pipeline_publishes_validated_synthetic_bundle(tmp_path):
    _, nodes, edges, transactions = graph_tables()
    edges["depth"] = edges.depth.astype("int8")
    data = tmp_path / "data"
    write_tables(data, nodes, edges, transactions)
    out = tmp_path / "outputs"

    first = run_pipeline(data, out, expected_nodes=len(nodes))
    assert load_bundle(out).manifest["run_id"] == first.name
    assert (first / "run.json").exists()
    second = run_pipeline(data, out, expected_nodes=len(nodes))
    assert load_bundle(out).manifest["run_id"] == second.name
    assert first.exists()
    for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_failed_candidate_does_not_replace_current(tmp_path, monkeypatch):
    _, nodes, edges, transactions = graph_tables()
    edges["depth"] = edges.depth.astype("int8")
    data = tmp_path / "data"
    write_tables(data, nodes, edges, transactions)
    out = tmp_path / "outputs"
    previous = run_pipeline(data, out, expected_nodes=len(nodes))
    before = (out / "current.json").read_bytes()
    monkeypatch.setattr("pipeline.subprocess.run", lambda *args, **kwargs: CompletedProcess(
        args, 1, "", "forced validator failure"
    ))

    with pytest.raises(ValueError, match="forced validator failure"):
        run_pipeline(data, out, expected_nodes=len(nodes))
    assert (out / "current.json").read_bytes() == before
    assert previous.exists()
