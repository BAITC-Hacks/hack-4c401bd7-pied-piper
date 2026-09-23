"""The evidence audit must detect factual errors, not just valid hashes."""
from datetime import date

import pandas as pd
import pytest

from pipeline import run_pipeline
from scripts import audit_evidence
from src.view import load_bundle


@pytest.fixture
def audited_run(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    ids = [10000000000000001, 10000000000000002, 10000000000000003]
    pd.DataFrame({"gid": ids, "depth": [0, 1, 2], "is_seed": [True, False, False]}).to_parquet(data / "nodes.parquet", index=False)
    edges = pd.DataFrame({"src": ids[:2], "dst": ids[1:],
                          "sum_kzt": [20000.0, 20000.0], "n_tx": [1, 1],
                          "depth": pd.Series([1, 2], dtype="int8")})
    edges.to_parquet(data / "edges.parquet", index=False)
    edges[["src", "dst", "sum_kzt"]].assign(date=date(2026, 7, 1))[
        ["src", "dst", "date", "sum_kzt"]
    ].to_parquet(data / "transactions.parquet", index=False)
    out = tmp_path / "out"
    run_pipeline(data, out, expected_nodes=3)
    return data, out


def test_audit_valid_facts_is_read_only(audited_run):
    data, out = audited_run
    before = {p: p.read_bytes() for root in (data, out) for p in root.rglob("*") if p.is_file()}
    report = audit_evidence.audit(data, out, expected_nodes=3)
    assert report["status"] == "passed"
    assert report["rows"] == report["why_rows"] == 3
    assert not report["errors"]
    assert before == {p: p.read_bytes() for root in (data, out) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("fault", ["amount", "caveat", "why"])
def test_audit_detects_text_errors_after_bundle_loading(audited_run, monkeypatch, fault):
    data, out = audited_run
    bundle = load_bundle(out)
    transit = bundle.nodes.index[bundle.nodes.role.eq("transit")][0]
    if fault == "amount":
        bundle.nodes.loc[transit, "evidence"] = bundle.nodes.loc[transit, "evidence"].replace("20000.00", "25000.00", 1)
        expected = "fact mismatch"
    elif fault == "caveat":
        bundle.nodes.loc[transit, "evidence"] = bundle.nodes.loc[transit, "evidence"].split(" Только")[0]
        expected = "missing sample caveat"
    else:
        bundle.top.loc[0, "why"] = "Приоритет: выдуманная причина +0.999."
        expected = "why contribution mismatch"
    # The audit's own semantic checks must reject the error even when an
    # upstream reader has accepted the bundle. No production files are changed.
    monkeypatch.setattr(audit_evidence, "load_bundle", lambda _: bundle)
    report = audit_evidence.audit(data, out, expected_nodes=3)
    assert report["status"] == "failed"
    assert expected in [error["issue"] for error in report["errors"]]
