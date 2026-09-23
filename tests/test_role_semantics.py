"""Controlled counterexamples for role rules and exported explanations.

Feature-level cases intentionally isolate one rule from graph centrality.
Graph-level cases below exercise the real feature calculation as well.
"""
import numpy as np
import pandas as pd
import pytest

from src.contracts import MAX_EVIDENCE_LENGTH, SUBSTANTIVE_ROLES
from src.engine import build_features, score_roles
from src.reporting import make_outputs


def feature_table(*overrides):
    rows = []
    for offset, changes in enumerate(overrides):
        row = {
            "gid": 10000000000000001 + offset,
            "depth": 1, "is_seed": False,
            "component_id": 0, "cluster_id": 0,
            "in_deg": 1, "out_deg": 1,
            "in_kzt": 10000.0, "out_kzt": 10000.0,
            "in_tx": 2, "out_tx": 2,
            "pagerank": 0.1, "betweenness": 0.0,
            "seed_distance": 1, "cross_cluster_degree": 0,
            "bridge_fraction": 0.0, "bridge": 0.0,
        }
        row.update(changes)
        row["at_boundary"] = row["depth"] == 4
        row["truncated_by_depth"] = row["at_boundary"] and row["out_deg"] == 0
        row["ratio_usable"] = row["in_kzt"] > 0 and not row["is_seed"] and not row["at_boundary"]
        row["pass_through"] = row["out_kzt"] / row["in_kzt"] if row["in_kzt"] else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("gid")


@pytest.mark.parametrize(("profile", "role", "fact"), [
    ({"in_deg": 3, "is_seed": True, "depth": 0}, "consolidator", "Вход от 3"),
    ({"out_deg": 3, "is_seed": True, "depth": 0}, "distributor", "Выход 3"),
    ({}, "transit", "отношение ≈1.00"),
    ({"out_deg": 0, "out_kzt": 0.0, "out_tx": 0}, "terminal", "исходящих связей 0"),
    ({"is_seed": True, "depth": 0, "betweenness": 0.2,
      "cross_cluster_degree": 2, "bridge_fraction": 1.0, "bridge": 1.0},
     "coordinator", "посредничество 0.20000"),
    ({"is_seed": True, "depth": 0}, "peripheral", "Вход/выход 1/1"),
])
def test_each_role_has_factual_export_and_reasons_for_rejected_roles(profile, role, fact):
    scored = score_roles(feature_table(profile))
    edges = pd.DataFrame(columns=["src", "dst", "sum_kzt"])
    roles, _, top, _ = make_outputs(scored, edges)
    assert roles.iloc[0].role == role
    assert fact in roles.iloc[0].evidence
    assert 0 < len(roles.iloc[0].evidence) <= MAX_EVIDENCE_LENGTH
    assert roles.iloc[0].evidence in top.iloc[0].why
    assert ("Seed: вход неполон." if profile.get("is_seed")
            else "Только наблюдаемые переводы ≥5000 KZT.") in roles.iloc[0].evidence
    for candidate in SUBSTANTIVE_ROLES:
        row = scored.iloc[0]
        if not row[f"eligible_{candidate}"]:
            assert row[f"score_{candidate}"] == 0
            assert row[f"ineligible_reason_{candidate}"]
        else:
            assert pd.isna(row[f"ineligible_reason_{candidate}"])


def test_perfect_balance_does_not_allow_transit_for_seed_or_boundary():
    scored = score_roles(feature_table({}, {"is_seed": True, "depth": 0}, {"depth": 4}))
    assert scored.eligible_transit.tolist() == [True, False, False]
    assert scored.score_transit.iloc[1:].eq(0).all()
    assert scored.iloc[2].role_score <= 0.5


def test_large_inflow_does_not_make_boundary_a_terminal():
    sink = {"out_deg": 0, "out_tx": 0, "out_kzt": 0.0, "in_kzt": 1e12}
    scored = score_roles(feature_table(sink, {**sink, "depth": 4}))
    assert scored.eligible_terminal.tolist() == [True, False]
    assert scored.iloc[1].score_terminal == 0
    roles, _, _, _ = make_outputs(scored, pd.DataFrame(columns=["src", "dst", "sum_kzt"]))
    assert "Depth=4: продолжение неизвестно." in roles.iloc[1].evidence


@pytest.mark.parametrize(("degree", "role", "threshold"), [
    ("in_deg", "consolidator", "in_degree_min"),
    ("out_deg", "distributor", "out_degree_min"),
])
def test_degree_gate_uses_population_quantile_not_a_fixed_two(degree, role, threshold):
    # Q75([1, 2, 2, 3, 8]) = 3: two counterparties no longer suffice.
    scored = score_roles(feature_table(*[{degree: value} for value in [1, 2, 2, 3, 8]]))
    assert scored.attrs["thresholds"][threshold] == 3
    assert scored[f"eligible_{role}"].tolist() == [False, False, False, True, True]


def test_large_pagerank_without_betweenness_cannot_make_coordinator():
    scored = score_roles(feature_table({"in_deg": 2, "out_deg": 2, "pagerank": 1.0}))
    assert scored.attrs["thresholds"]["coordinator_betweenness_min"] is None
    assert not scored.iloc[0].eligible_coordinator
    assert scored.iloc[0].score_coordinator == 0


def test_equal_role_strengths_have_stable_tie_break_and_reduced_support():
    # Symmetric seed: consolidator/distributor score 0.9, transit excluded.
    scored = score_roles(feature_table({"in_deg": 2, "out_deg": 2, "is_seed": True, "depth": 0}))
    row = scored.iloc[0]
    assert row.role == "consolidator"
    assert row.alternative_role == "distributor"
    assert row.alternative_score == pytest.approx(0.9)
    assert row.role_margin == pytest.approx(0.0)
    assert row.role_score == pytest.approx(0.495)


def test_all_isolates_have_clusters_and_no_role_priority():
    nodes = pd.DataFrame({"gid": [19, 2, 10], "depth": [0, 0, 0], "is_seed": [True] * 3})
    edges = pd.DataFrame({
        "src": pd.Series(dtype="int64"), "dst": pd.Series(dtype="int64"),
        "sum_kzt": pd.Series(dtype="float64"), "n_tx": pd.Series(dtype="int64"),
    })
    scored = score_roles(build_features(nodes, edges, pd.DataFrame())[0])
    assert scored.index.tolist() == [2, 10, 19]
    assert scored.cluster_id.tolist() == [0, 1, 2]
    assert scored.component_id.tolist() == [0, 1, 2]
    assert scored.role.eq("peripheral").all()
    assert scored.role_score.eq(0.5).all()
    assert scored.priority_role_support.eq(0).all()
    assert scored.pass_through.isna().all()
    assert all(value is None for value in scored.attrs["thresholds"].values())
    assert np.isfinite(scored.priority_score).all()


def test_export_ties_use_numeric_gid_and_cluster_sums_count_directed_edges_once():
    features = feature_table({"gid": 19}, {"gid": 2}, {"gid": 10, "cluster_id": 1})
    scored = score_roles(features)
    edges = pd.DataFrame({
        "src": [19, 2, 19], "dst": [2, 19, 10],
        "sum_kzt": [10000.0, 6000.0, 5000.0],
    })
    _, clusters, top, _ = make_outputs(scored, edges)
    assert top.gid.tolist() == [2, 10, 19]
    assert top["rank"].tolist() == [1, 2, 3]
    summary = clusters.set_index("cluster_id")
    assert summary.loc[0, "top_gids"] == "2|19"
    assert summary.loc[0, "sum_kzt_internal"] == 16000.0
    assert summary.loc[1, "sum_kzt_internal"] == 0.0
    # This controlled profile has magnitude=.3 and role_support=.15;
    # seed proximity=.1 and structure=.0875 must not displace them in why.
    assert top.why.str.contains("объём +0.300; поддержка роли +0.150", regex=False).all()
