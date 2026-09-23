"""Build, independently validate and publish an immutable AML review bundle."""
import argparse
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from uuid import uuid4
from contextlib import nullcontext

from src.contracts import (
    CSV_OPTIONS, INPUT_SCHEMAS, OUTPUT_SCHEMAS, PRIORITY_WEIGHTS,
    RANDOM_SEED, ROLES, SCHEMA_VERSION,
)
from src.data import load_data
from src.engine import build_features, score_roles
from src.reporting import make_outputs


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def run_pipeline(data: Path, out: Path, expected_nodes: int = 2248, *,
                 run_id: str | None = None, on_stage=None, publication_guard=None) -> Path:
    """Publish a run only after its staging candidate passes validate.py."""
    start = time.monotonic()
    from src.bundle_io import uuid_text
    run_id = uuid_text(run_id) if run_id is not None else str(uuid4())
    stage = on_stage or (lambda name: None)
    data, out = Path(data), Path(out)
    stage("load")
    nodes, edges, transactions, diagnostics = load_data(data, expected_nodes=expected_nodes)
    loaded = time.monotonic()
    stage("features")
    features, graph = build_features(nodes, edges, transactions)
    featured = time.monotonic()
    stage("roles")
    scored = score_roles(features)
    thresholds = scored.attrs["thresholds"]
    scales = scored.attrs["normalization_scales"]
    scored_at = time.monotonic()
    stage("export")
    roles, clusters, top, metrics = make_outputs(scored, edges)
    candidate = out / ".staging" / run_id
    candidate.mkdir(parents=True, exist_ok=False)
    for filename, frame in (
        ("nodes_roles.csv", roles), ("clusters.csv", clusters), ("top_nodes.csv", top)
    ):
        frame.to_csv(candidate / filename, **CSV_OPTIONS)
    metrics.to_parquet(candidate / "node_metrics.parquet", index=False)
    edges.to_parquet(candidate / "edges.parquet", index=False)
    exported = time.monotonic()
    counts = {
        "nodes": len(nodes), "edges": len(edges), "transactions": len(transactions),
        "seeds": int(nodes.is_seed.sum()),
        "components": int(scored.component_id.nunique()),
        "clusters": int(scored.cluster_id.nunique()),
    }
    stages = {
        "load": loaded - start, "features": featured - loaded,
        "roles": scored_at - featured, "export": exported - scored_at,
        "validation": 0.0, "total": exported - start,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "status": "candidate",
        "input_sha256": {name: _hash(data / name) for name in INPUT_SCHEMAS},
        "output_sha256": {name: _hash(candidate / name) for name in OUTPUT_SCHEMAS},
        "counts": counts,
        "versions": {
            "python": platform.python_version(), "methodology": "lane-a-v1",
            **{name: importlib.metadata.version(name) for name in
               ("numpy", "pandas", "pyarrow", "networkx", "scipy")},
        },
        "seed": RANDOM_SEED, "thresholds": thresholds,
        "normalization_scales": scales, "priority_weights": PRIORITY_WEIGHTS,
        "stage_runtimes_seconds": stages,
        "role_distribution": {role: int(scored.role.eq(role).sum()) for role in ROLES},
        "warnings": (
            ["Синтетический набор; выводы не относятся к реальным клиентам."]
            if expected_nodes != 2248 else []
        ),
    }
    if expected_nodes != 2248:
        manifest["data_kind"] = "synthetic"
    _write_json(candidate / "candidate.json", manifest)
    command = [
        sys.executable, "-X", "utf8", str(Path(__file__).with_name("validate.py")),
        "--data", str(data.resolve()), "--out", str(candidate.resolve()),
        "--candidate", "--expected-nodes", str(expected_nodes),
    ]
    validation_start = time.monotonic()
    stage("validation")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=300)
    stages["validation"] = time.monotonic() - validation_start
    stages["total"] = time.monotonic() - start
    if result.returncode:
        raise ValueError(f"Candidate {candidate} не прошёл validate.py: {result.stderr.strip()}")
    report = json.loads(result.stdout)
    if report.get("status") != "passed" or stages["total"] >= 300:
        raise ValueError(f"Candidate {candidate}: проверка не пройдена или runtime >=300 с")
    manifest["status"] = "complete"
    manifest["validation"] = {
        "status": "passed", "validator_version": report["validator_version"],
    }
    (candidate / "candidate.json").unlink()
    _write_json(candidate / "run.json", manifest)
    runs = out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    published = runs / run_id
    stage("publish")
    # A worker may fence publication with its durable lease; CLI needs no guard.
    with publication_guard() if publication_guard else nullcontext():
        if published.exists():
            raise ValueError("Run already published")
        candidate.replace(published)
        pointer = out / f"current.{run_id}.tmp"
        _write_json(pointer, {"schema_version": SCHEMA_VERSION, "run_id": run_id})
        os.replace(pointer, out / "current.json")
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    parser.add_argument("--expected-nodes", type=int, default=2248)
    args = parser.parse_args()
    try:
        published = run_pipeline(args.data, args.out, args.expected_nodes)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Ошибка pipeline: {exc}\n")
    print(json.dumps({"status": "published", "run": str(published)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
