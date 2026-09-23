"""HTTP schema; analytical file schemas remain in backend.core.contracts."""
from typing import Annotated, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, StrictStr, AfterValidator
from uuid import UUID
import re


def canonical_uuid(value):
    if str(UUID(value)) != value:
        raise ValueError("Canonical UUID required")
    return value


def canonical_gid(value):
    if not re.fullmatch(r"0|-?[1-9][0-9]*", value) or not -(2**63) <= int(value) < 2**63:
        raise ValueError("Canonical int64 string required")
    return value


Identifier = Annotated[StrictStr, AfterValidator(canonical_uuid)]
Gid = Annotated[StrictStr, AfterValidator(canonical_gid)]
Role = Literal['consolidator', 'distributor', 'transit', 'terminal', 'coordinator', 'peripheral']
SubstantiveRole = Literal['consolidator', 'distributor', 'transit', 'terminal', 'coordinator']
Component = Literal['structure', 'seed_proximity', 'magnitude', 'role_support']
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ErrorDetail(Model):
    field: str | None = None
    file: str | None = None
    row: int | None = None
    code: str
    message: str


class ErrorBody(Model):
    code: str
    message: str
    details: list[ErrorDetail]
    retryable: bool
    related_run_id: Identifier | None


class ApiError(Model):
    error: ErrorBody
    request_id: str


T = TypeVar('T')


class Page(Model, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class RunPage(Page[T], Generic[T]):
    run_id: Identifier


class InputCounts(Model):
    nodes: int
    edges: int
    transactions: int
    seeds: int


class Counts(InputCounts):
    components: int
    clusters: int


class Collection(Model):
    max_depth: Literal[4]
    min_amount_kzt: Literal[5000]
    direction: Literal['outgoing']


class Period(Model):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    from_: str = Field(alias='from')
    to: str


class Validation(Model):
    errors: list[ErrorDetail]
    warnings: list[str]


class Dataset(Model):
    dataset_id: Identifier
    name: str
    status: Literal['validating', 'ready', 'invalid', 'failed']
    profile: Literal['hackathon-v1']
    data_kind: Literal['official', 'synthetic']
    created_at: str
    counts: InputCounts | None
    period: Period | None
    collection: Collection
    validation: Validation
    failure: ErrorBody | None


class Run(Model):
    run_id: Identifier
    dataset_id: Identifier
    status: Literal['queued', 'running', 'succeeded', 'failed']
    stage: Literal['load', 'features', 'roles', 'export', 'validation', 'publish'] | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    elapsed_seconds: float
    methodology_version: str
    error: ErrorBody | None


class Thresholds(Model):
    in_degree_min: int | None
    out_degree_min: int | None
    terminal_in_kzt_min: float | None
    transit_balance_min: float | None
    coordinator_betweenness_min: float | None


class Summary(Model):
    run_id: Identifier
    dataset_id: Identifier
    data_kind: Literal['official', 'synthetic']
    schema_version: str
    methodology_version: str
    counts: Counts
    role_distribution: dict[Role, int]
    boundary_nodes: int
    isolated_nodes: int
    warnings: list[str]
    priority_weights: dict[Component, float]
    thresholds: Thresholds


class Warning(Model):
    code: Literal['BOUNDARY_DEPTH', 'SEED_INFLOW_INCOMPLETE', 'ZERO_OBSERVED_INFLOW',
                  'ISOLATED_NODE', 'SAMPLE_INCOMPLETE']
    message: str


class NodeSummary(Model):
    gid: Gid
    role: Role
    role_score: Score
    priority_score: Score
    cluster_id: int
    component_id: int
    depth: int
    is_seed: bool
    at_boundary: bool
    truncated_by_depth: bool
    evidence: str
    warnings: list[Warning]


class Metrics(Model):
    in_deg: int
    out_deg: int
    in_kzt: float
    out_kzt: float
    in_tx: int
    out_tx: int
    pass_through: float | None
    ratio_usable: bool
    pagerank: float
    betweenness: float
    seed_distance: int | None
    cross_cluster_degree: int
    bridge_fraction: float
    bridge: float


class Support(Model):
    eligible: bool
    score: Score
    ineligible_reason: str | None


class Contribution(Model):
    value: Score
    weight: Score
    contribution: Score


class NodeDetail(NodeSummary):
    run_id: Identifier
    metrics: Metrics
    alternative_role: SubstantiveRole | None
    alternative_score: Score
    role_margin: Score
    role_support: dict[SubstantiveRole, Support]
    priority: dict[Component, Contribution]


class RankedNode(NodeSummary):
    rank: int
    why: str


class Edge(Model):
    src: Gid
    dst: Gid
    sum_kzt: float
    n_tx: int
    depth: int


class Cluster(Model):
    cluster_id: int
    component_id: int
    n_nodes: int
    n_seed: int
    sum_kzt_internal: float
    top_gids: list[Gid]
    hypothesis: str


class ClusterDetail(Cluster):
    run_id: Identifier


class Truncation(Model):
    total_nodes: int
    total_edges: int
    hidden_nodes: int
    hidden_edges: int
    node_limit: int
    edge_limit: int


class Graph(Model):
    run_id: Identifier
    center_gid: Gid
    hops: Literal[1, 2]
    nodes: list[NodeSummary]
    edges: list[Edge]
    truncation: Truncation


class ClusterEdge(Model):
    src_cluster_id: int
    dst_cluster_id: int
    sum_kzt: float
    n_tx: int


class ClusterGraph(Model):
    run_id: Identifier
    nodes: list[Cluster]
    edges: list[ClusterEdge]
    truncation: Truncation


class Selection(Model):
    run_id: Identifier
    gids: list[Gid]
    updated_at: str | None


class PutSelection(Model):
    gids: list[Gid] = Field(max_length=10000)


class CreateRun(Model):
    dataset_id: Identifier


class Paging(Model):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class RunQuery(Paging):
    dataset_id: Identifier


class NodeQuery(Paging):
    role: list[Role] = Field(default_factory=list)
    cluster_id: int | None = Field(default=None, ge=0)
    component_id: int | None = Field(default=None, ge=0)
    min_priority: Score | None = None
    is_seed: Literal['true', 'false'] | None = None
    at_boundary: Literal['true', 'false'] | None = None
    sort: Literal['priority_desc', 'role_score_desc', 'gid_asc'] = 'priority_desc'


class EdgeQuery(Paging):
    direction: Literal['in', 'out', 'both'] = 'both'


class TopQuery(Paging):
    limit: int = Field(default=20, ge=1, le=200)


class GraphLimits(Model):
    node_limit: int = Field(default=100, ge=1, le=100)
    edge_limit: int = Field(default=350, ge=1, le=350)


class GraphQuery(GraphLimits):
    hops: int = Field(default=1, ge=1, le=2)
