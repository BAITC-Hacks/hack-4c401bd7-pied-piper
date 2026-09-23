/** Frontend/API contract 1.0.0. HTTP backend: backend/, setup: docs/backend.md.
 * gid/src/dst are opaque decimal strings: never convert them to JS Number.
 * Dates are YYYY-MM-DD; timestamps are RFC3339 UTC strings.
 */
export type UUID = string;
export type Gid = string;
export type Role = 'consolidator' | 'distributor' | 'transit' | 'terminal'
    | 'coordinator' | 'peripheral';
export type SubstantiveRole = Exclude<Role, 'peripheral'>;
export type PriorityComponent = 'structure' | 'seed_proximity' | 'magnitude' | 'role_support';
export type WarningCode = 'BOUNDARY_DEPTH' | 'SEED_INFLOW_INCOMPLETE'
    | 'ZERO_OBSERVED_INFLOW' | 'ISOLATED_NODE' | 'SAMPLE_INCOMPLETE';
export interface Warning { code: WarningCode; message: string }

export type ErrorCode = 'INVALID_REQUEST' | 'UPLOAD_TOO_LARGE' | 'UNSUPPORTED_FILE_TYPE'
    | 'DATASET_INVALID' | 'DATASET_NOT_READY' | 'NOT_FOUND' | 'RUN_NOT_READY'
    | 'ACTIVE_RUN_EXISTS' | 'IDEMPOTENCY_CONFLICT' | 'RESULT_INVALID'
    | 'SELECTION_LIMIT' | 'SERVICE_UNAVAILABLE' | 'INTERNAL_ERROR';
export interface ErrorDetail {
    field: string | null;
    file: string | null;
    row: number | null; // 1-based data row, excluding header
    code: string;
    message: string;
}
export interface ErrorBody {
    code: ErrorCode;
    message: string;
    details: ErrorDetail[];
    retryable: boolean;
    related_run_id: UUID | null;
}
export interface ApiError { error: ErrorBody; request_id: string }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface RunPage<T> extends Page<T> { run_id: UUID }
export interface InputCounts { nodes: number; edges: number; transactions: number; seeds: number }
export interface Counts extends InputCounts { components: number; clusters: number }
export interface Dataset {
    dataset_id: UUID;
    name: string;
    status: 'validating' | 'ready' | 'invalid' | 'failed';
    profile: 'hackathon-v1';
    data_kind: 'official' | 'synthetic';
    created_at: string;
    counts: InputCounts | null;
    period: { from: string; to: string } | null;
    collection: { max_depth: 4; min_amount_kzt: 5000; direction: 'outgoing' };
    validation: { errors: ErrorDetail[]; warnings: string[] };
    failure: ErrorBody | null;
}
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed';
export type RunStage = 'load' | 'features' | 'roles' | 'export' | 'validation' | 'publish';
export interface Run {
    run_id: UUID;
    dataset_id: UUID;
    status: RunStatus;
    stage: RunStage | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
    elapsed_seconds: number;
    methodology_version: string;
    error: ErrorBody | null;
}
export interface RunSummary {
    run_id: UUID;
    dataset_id: UUID;
    data_kind: 'official' | 'synthetic';
    schema_version: string; // Existing bundle version, distinct from API version.
    methodology_version: string;
    counts: Counts;
    role_distribution: Record<Role, number>;
    boundary_nodes: number;
    isolated_nodes: number;
    warnings: string[];
    priority_weights: Record<PriorityComponent, number>;
    thresholds: {
        in_degree_min: number | null;
        out_degree_min: number | null;
        terminal_in_kzt_min: number | null;
        transit_balance_min: number | null;
        coordinator_betweenness_min: number | null;
    };
}
export interface NodeSummary {
    gid: Gid;
    role: Role;
    role_score: number;
    priority_score: number;
    cluster_id: number;
    component_id: number;
    depth: number;
    is_seed: boolean;
    at_boundary: boolean;
    truncated_by_depth: boolean;
    evidence: string;
    warnings: Warning[];
}
export interface NodeMetrics {
    in_deg: number;
    out_deg: number;
    in_kzt: number;
    out_kzt: number;
    in_tx: number;
    out_tx: number;
    pass_through: number | null;
    ratio_usable: boolean;
    pagerank: number;
    betweenness: number;
    seed_distance: number | null;
    cross_cluster_degree: number;
    bridge_fraction: number;
    bridge: number;
}
export interface NodeDetail extends NodeSummary {
    run_id: UUID;
    metrics: NodeMetrics;
    alternative_role: SubstantiveRole | null;
    alternative_score: number;
    role_margin: number;
    role_support: Record<SubstantiveRole, {
        eligible: boolean;
        score: number;
        ineligible_reason: string | null;
    }>;
    priority: Record<PriorityComponent, { value: number; weight: number; contribution: number }>;
}
export interface RankedNode extends NodeSummary { rank: number; why: string }
export interface Edge { src: Gid; dst: Gid; sum_kzt: number; n_tx: number; depth: number }
export interface Cluster {
    cluster_id: number;
    component_id: number;
    n_nodes: number;
    n_seed: number;
    sum_kzt_internal: number;
    top_gids: Gid[];
    hypothesis: string;
}
export interface ClusterDetail extends Cluster { run_id: UUID }
export interface GraphResult {
    run_id: UUID;
    center_gid: Gid;
    hops: 1 | 2;
    nodes: NodeSummary[];
    edges: Edge[];
    truncation: {
        total_nodes: number;
        total_edges: number;
        hidden_nodes: number;
        hidden_edges: number;
        node_limit: number;
        edge_limit: number;
    };
}
export interface ClusterGraph {
    run_id: UUID;
    nodes: Cluster[];
    edges: { src_cluster_id: number; dst_cluster_id: number; sum_kzt: number; n_tx: number }[];
    truncation: {
        total_nodes: number;
        total_edges: number;
        hidden_nodes: number;
        hidden_edges: number;
        node_limit: number;
        edge_limit: number;
    };
}
export interface Selection { run_id: UUID; gids: Gid[]; updated_at: string | null }
export interface PutSelectionBody { gids: Gid[] }
export interface CreateRunBody { dataset_id: UUID }
export type ExportFile = 'nodes_roles.csv' | 'clusters.csv' | 'top_nodes.csv';

// All fields optional; repeated role query parameters use OR, other filters AND.
export interface NodeQuery {
    role?: Role[];
    cluster_id?: number;
    component_id?: number;
    min_priority?: number;
    is_seed?: boolean;
    at_boundary?: boolean;
    sort?: 'priority_desc' | 'role_score_desc' | 'gid_asc';
    limit?: number;
    offset?: number;
}
export interface EdgeQuery {
    direction?: 'in' | 'out' | 'both';
    limit?: number;
    offset?: number;
}
