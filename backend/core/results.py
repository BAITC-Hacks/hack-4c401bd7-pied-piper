"""Validated bundle access for CLI and backend, independent of visualization."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import numpy as np
import pandas as pd
from backend.validate import SCHEMAS, ValidationError, ids, read_csv, require, validate_frames

@dataclass
class Bundle:
    nodes: pd.DataFrame
    edges: pd.DataFrame
    clusters: pd.DataFrame
    top: pd.DataFrame
    manifest: dict
    report: dict
    raw: dict[str, bytes]


def load_bundle(directory: Path, candidate=False) -> Bundle:
    from backend.core.bundle_io import read_bundle_files
    from backend.core.contracts import OUTPUT_SCHEMAS
    from backend.validate import check_schema, check_metrics, SCORE_ATOL
    try:
        _, manifest, raw = read_bundle_files(directory, candidate=candidate)
        frames = [read_csv(BytesIO(raw[name]), name) for name in SCHEMAS]
        for frame, name in zip(frames, SCHEMAS):
            check_schema(frame, OUTPUT_SCHEMAS[name], name, csv=True)
        metrics = pd.read_parquet(BytesIO(raw['node_metrics.parquet']))
        edges = pd.read_parquet(BytesIO(raw['edges.parquet']))
        for name, frame in [('node_metrics.parquet',metrics), ('edges.parquet',edges)]:
            check_schema(frame, OUTPUT_SCHEMAS[name], name)
        roles, clusters, top, report = validate_frames(*frames, metrics[['gid','depth','is_seed']], edges, manifest['counts']['nodes'])
        for frame in (roles, metrics):
            require(frame.gid.map(int).tolist() == sorted(frame.gid.map(int)), 'Строки nodes/metrics должны быть отсортированы по числовому gid')
        require(clusters.cluster_id.tolist() == sorted(clusters.cluster_id), 'clusters: неверная сортировка')
        require(list(zip(edges.src,edges.dst)) == sorted(zip(edges.src,edges.dst)), 'edges: неверная сортировка')
        metrics['gid'] = ids(metrics.gid)
        for col in ('src','dst'):
            edges[col] = ids(edges[col],col)
        require(not metrics.gid.duplicated().any(), 'node_metrics: повторные gid')
        for name in SCHEMAS['nodes_roles.csv'][1:]:
            left = metrics.set_index('gid').loc[roles.gid,name].reset_index(drop=True)
            right = roles[name].reset_index(drop=True)
            if pd.api.types.is_numeric_dtype(right):
                require(np.allclose(left,right,rtol=0,atol=SCORE_ATOL), f'node_metrics: отличается {name}')
            else:
                require(left.astype(str).tolist()==right.astype(str).tolist(), f'node_metrics: отличается {name}')
        check_metrics(metrics, edges, manifest, report)
        return Bundle(metrics, edges, clusters, top, manifest, report, raw)
    except ValidationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        raise ValidationError(f'Нет корректного завершённого расчёта в {directory}: {exc}') from exc
