"""Read-only audit of exported evidence against source edges and saved metrics.

This checks facts and display rounding, not the validity of AML hypotheses.
No scoring or reporting functions are called; nothing is written to the bundle.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.contracts import MAX_EVIDENCE_LENGTH, PRIORITY_WEIGHTS
from src.view import load_bundle
from validate import check_inputs


def audit(data: Path, out: Path, expected_nodes: int = 2248) -> dict:
    bundle = load_bundle(out)
    check_inputs(bundle, data, expected_nodes)
    edges = pd.read_parquet(data / "edges.parquet")
    metrics = bundle.nodes.set_index("gid")
    inbound = edges.groupby("dst").agg(amount=("sum_kzt", "sum"), degree=("src", "nunique"))
    outbound = edges.groupby("src").agg(amount=("sum_kzt", "sum"), degree=("dst", "nunique"))
    errors = []

    def require(ok, gid, message):
        if not ok:
            errors.append({"gid": str(gid), "issue": message})

    def close(actual, expected, tolerance):
        return math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance)

    for row in bundle.nodes.itertuples(index=False):
        gid = int(row.gid)
        incoming = float(inbound.at[gid, "amount"]) if gid in inbound.index else 0.0
        outgoing = float(outbound.at[gid, "amount"]) if gid in outbound.index else 0.0
        in_degree = int(inbound.at[gid, "degree"]) if gid in inbound.index else 0
        out_degree = int(outbound.at[gid, "degree"]) if gid in outbound.index else 0
        text = row.evidence
        require(0 < len(text) <= MAX_EVIDENCE_LENGTH, gid, "evidence length")
        if row.at_boundary:
            require("Depth=4: продолжение неизвестно." in text, gid, "missing boundary caveat")
        elif row.is_seed:
            require("Seed: вход неполон." in text, gid, "missing seed caveat")
        elif incoming == 0:
            require("отношение не определено" in text, gid, "missing undefined-ratio caveat")
        else:
            require("Только наблюдаемые переводы ≥5000 KZT." in text, gid, "missing sample caveat")
        # Parse role-specific numbers, independently comparing money/degrees
        # to original edges. Centrality/bridge refer to validated saved metrics.
        facts = text.split(" KZT.", 1)[0] if row.role in ("consolidator", "distributor", "peripheral") else text
        patterns = {
            "consolidator": r"Вход от (\d+) контр\.: ([\d.]+) KZT; выход ([\d.]+)",
            "distributor": r"Выход (\d+) контр\.: ([\d.]+) KZT; вход ([\d.]+)",
            "transit": r"Вход/выход: ([\d.]+)/([\d.]+) KZT; отношение ≈([\d.]+)\.",
            "terminal": r"Вход ([\d.]+) KZT от (\d+) контр\.; наблюдаемых исходящих связей (\d+)\.",
            "coordinator": r"Прибл\. посредничество ([\d.]+); внешних соседей (\d+); вход/выход (\d+)/(\d+) контр\.",
            "peripheral": r"Вход/выход (\d+)/(\d+) контр\.; потоки ([\d.]+)/([\d.]+)",
        }
        match = re.match(patterns[row.role], facts)
        require(match is not None, gid, "unrecognized factual text")
        if not match:
            continue
        actual = [float(value) for value in match.groups()]
        if row.role == "consolidator":
            expected, tolerances = [in_degree, incoming, outgoing], [0, .00501, .00501]
        elif row.role == "distributor":
            expected, tolerances = [out_degree, outgoing, incoming], [0, .00501, .00501]
        elif row.role == "transit":
            expected, tolerances = [incoming, outgoing, outgoing / incoming if incoming else math.nan], [.00501] * 3
        elif row.role == "terminal":
            expected, tolerances = [incoming, in_degree, out_degree], [.00501, 0, 0]
        elif row.role == "coordinator":
            expected = [row.betweenness, row.cross_cluster_degree, in_degree, out_degree]
            tolerances = [.00000501, 0, 0, 0]
        else:
            expected, tolerances = [in_degree, out_degree, incoming, outgoing], [0, 0, .00501, .00501]
        require(all(close(a, e, t) for a, e, t in zip(actual, expected, tolerances)), gid, "fact mismatch")

    labels = {"structure": "структура", "seed_proximity": "близость seed",
              "magnitude": "объём", "role_support": "поддержка роли"}
    for row in bundle.top.itertuples(index=False):
        stored = metrics.loc[row.gid]
        require(row.why.startswith(stored.evidence + " Приоритет: "), row.gid, "why loses evidence")
        parts = sorted(PRIORITY_WEIGHTS, key=lambda key: -stored[f"contribution_{key}"])[:2]
        explanation = "; ".join(f"{labels[key]} +{stored[f'contribution_{key}']:.3f}" for key in parts) + "."
        require(row.why.endswith(explanation), row.gid, "why contribution mismatch")

    nodes = bundle.nodes
    examples = {}
    for role, group in nodes.groupby("role"):
        row = group.sort_values(["priority_score", "gid"], ascending=[False, True]).iloc[0]
        examples[role] = {"gid": row.gid, "evidence": row.evidence,
                          "alternative_role": row.alternative_role if pd.notna(row.alternative_role) else None,
                          "role_margin": float(row.role_margin)}
    return {
        "status": "passed" if not errors else "failed",
        "run_id": bundle.manifest["run_id"], "rows": len(nodes), "why_rows": len(bundle.top),
        "length_min": int(nodes.evidence.str.len().min()),
        "length_max": int(nodes.evidence.str.len().max()),
        "unique_evidence": int(nodes.evidence.nunique()),
        "role_counts": dict(Counter(nodes.role)),
        "seed_rows": int(nodes.is_seed.sum()), "boundary_rows": int(nodes.at_boundary.sum()),
        "ambiguous_substantive_rows": int((nodes.role.ne("peripheral") & nodes.role_margin.lt(.05)).sum()),
        "thresholds": bundle.manifest["thresholds"],
        "runtime_seconds": bundle.manifest["stage_runtimes_seconds"]["total"],
        "top_1": bundle.top.iloc[0].to_dict(), "examples": examples, "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    parser.add_argument("--expected-nodes", type=int, default=2248)
    args = parser.parse_args()
    try:
        report = audit(args.data, args.out, args.expected_nodes)
    except (ValueError, OSError, KeyError, TypeError, AssertionError) as exc:
        parser.exit(1, f"Audit failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
