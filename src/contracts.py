"""Contract v1 shared by A (producer), B (validator/UI), and P (acceptance).

Declarative schemas only: this module does not calculate scores or validate data.
Types name storage types, not pandas inference. See PLAN.md section 7.
"""
from dataclasses import dataclass
from typing import TypedDict


SCHEMA_VERSION = "1.0.0"
EXPECTED_NODE_COUNT = 2248
MIN_TOP_ROWS = 20
MAX_EVIDENCE_LENGTH = 200
RANDOM_SEED = 42
SUM_RTOL = 1e-9
SUM_ATOL_KZT = 0.01
SCORE_ATOL = 1e-12

# This order also resolves exact ties between eligible substantive roles.
ROLES = (
    "consolidator", "distributor", "transit", "terminal", "coordinator",
    "peripheral",
)
SUBSTANTIVE_ROLES = ROLES[:-1]
PRIORITY_WEIGHTS = {
    "structure": 0.35,
    "seed_proximity": 0.20,
    "magnitude": 0.30,
    "role_support": 0.15,
}


@dataclass(frozen=True)
class Field:
    name: str
    dtype: str
    nullable: bool = False


NODES_SCHEMA = (
    Field("gid", "int64"), Field("depth", "int64"), Field("is_seed", "bool"),
)
EDGES_SCHEMA = (
    Field("src", "int64"), Field("dst", "int64"),
    Field("sum_kzt", "float64"), Field("n_tx", "int64"), Field("depth", "int8"),
)
TRANSACTIONS_SCHEMA = (
    Field("src", "int64"), Field("dst", "int64"),
    Field("date", "date"), Field("sum_kzt", "float64"),
)
INPUT_SCHEMAS = {
    "nodes.parquet": NODES_SCHEMA,
    "edges.parquet": EDGES_SCHEMA,
    "transactions.parquet": TRANSACTIONS_SCHEMA,
}

NODES_ROLES_SCHEMA = (
    Field("gid", "int64"), Field("role", "string"),
    Field("role_score", "float64"), Field("cluster_id", "int64"),
    Field("priority_score", "float64"), Field("evidence", "string"),
)
CLUSTERS_SCHEMA = (
    Field("cluster_id", "int64"), Field("n_nodes", "int64"),
    Field("n_seed", "int64"), Field("sum_kzt_internal", "float64"),
    Field("top_gids", "string"), Field("hypothesis", "string"),
)
TOP_NODES_SCHEMA = (
    Field("rank", "int64"), Field("gid", "int64"), Field("role", "string"),
    Field("priority_score", "float64"), Field("why", "string"),
)
NODE_METRICS_SCHEMA = (
    *NODES_ROLES_SCHEMA,
    Field("depth", "int64"), Field("is_seed", "bool"),
    Field("component_id", "int64"),
    *(Field(name, "int64") for name in ("in_deg", "out_deg", "in_tx", "out_tx")),
    *(Field(name, "float64") for name in ("in_kzt", "out_kzt", "pagerank", "betweenness")),
    Field("pass_through", "float64", nullable=True),
    Field("at_boundary", "bool"), Field("truncated_by_depth", "bool"),
    Field("ratio_usable", "bool"), Field("seed_distance", "int64", nullable=True),
    Field("cross_cluster_degree", "int64"),
    Field("bridge_fraction", "float64"), Field("bridge", "float64"),
    *(Field(f"score_{role}", "float64") for role in ROLES),
    *(Field(f"eligible_{role}", "bool") for role in SUBSTANTIVE_ROLES),
    *(Field(f"ineligible_reason_{role}", "string", nullable=True)
      for role in SUBSTANTIVE_ROLES),
    Field("alternative_role", "string", nullable=True),
    Field("alternative_score", "float64"), Field("role_margin", "float64"),
    *(Field(f"priority_{name}", "float64") for name in PRIORITY_WEIGHTS),
    *(Field(f"contribution_{name}", "float64") for name in PRIORITY_WEIGHTS),
)
OUTPUT_SCHEMAS = {
    "nodes_roles.csv": NODES_ROLES_SCHEMA,
    "clusters.csv": CLUSTERS_SCHEMA,
    "top_nodes.csv": TOP_NODES_SCHEMA,
    "node_metrics.parquet": NODE_METRICS_SCHEMA,
    "edges.parquet": EDGES_SCHEMA,
}
REQUIRED_BUNDLE_FILES = (*OUTPUT_SCHEMAS, "run.json")
CSV_OPTIONS = {"encoding": "utf-8", "index": False, "lineterminator": "\n"}


def column_names(schema: tuple[Field, ...]) -> tuple[str, ...]:
    """Required column order; additive columns follow these fields."""
    return tuple(field.name for field in schema)


class Counts(TypedDict):
    nodes: int
    edges: int
    transactions: int
    seeds: int
    components: int
    clusters: int


class Thresholds(TypedDict):
    in_degree_min: int | None
    out_degree_min: int | None
    terminal_in_kzt_min: float | None
    transit_balance_min: float | None
    coordinator_betweenness_min: float | None


class ValidationResult(TypedDict):
    status: str  # Exactly "passed" for a published bundle.
    validator_version: str


class RunManifest(TypedDict):
    schema_version: str
    run_id: str
    status: str  # Exactly "complete" for a published bundle.
    input_sha256: dict[str, str]
    output_sha256: dict[str, str]
    counts: Counts
    versions: dict[str, str]
    seed: int
    thresholds: Thresholds
    normalization_scales: dict[str, float]
    priority_weights: dict[str, float]
    stage_runtimes_seconds: dict[str, float]
    role_distribution: dict[str, int]
    warnings: list[str]
    validation: ValidationResult
