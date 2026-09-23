"""Versioned bundle protocol: immutable run directories and staging candidates."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import re
from uuid import UUID

from src.contracts import (SCHEMA_VERSION, INPUT_SCHEMAS, OUTPUT_SCHEMAS, RunManifest,
                           Counts, Thresholds, PRIORITY_WEIGHTS, RANDOM_SEED, ROLES)


class ValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def json_object(raw, label):
    def reject(value):
        raise ValidationError(f'{label}: недопустимое JSON значение {value}')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'{label}: повторный ключ {key}')
            result[key] = value
        return result
    try:
        obj = json.loads(raw, parse_constant=reject, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError(f'{label}: неверный JSON: {exc}') from exc
    require(isinstance(obj, dict), f'{label}: ожидается объект')
    return obj


def uuid_text(value):
    try:
        require(isinstance(value, str) and str(UUID(value)) == value, 'run_id: требуется канонический UUID')
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('run_id: требуется канонический UUID, не путь') from exc
    return value


def resolve_run(root: Path, candidate=False):
    """Resolve current.json once. A direct run/staging directory is also accepted."""
    root = Path(root)
    if candidate:
        require(not (root/'current.json').exists(), '--candidate требует каталог staging, не корень публикации')
        return root, None
    if (root/'current.json').exists():
        pointer = json_object((root/'current.json').read_bytes(), 'current.json')
        require(pointer.get('schema_version') == SCHEMA_VERSION, 'current.json: несовместимая schema_version')
        run_id = uuid_text(pointer.get('run_id'))
        directory = root/'runs'/run_id
        require(directory.resolve().parent == (root/'runs').resolve(), 'current.json: каталог запуска вне runs')
        return directory, run_id
    return root, None


def check_manifest(manifest, candidate=False):
    label = 'candidate.json' if candidate else 'run.json'
    required = set(RunManifest.__annotations__) - ({'validation'} if candidate else set())
    require(required <= set(manifest), f'{label}: отсутствуют поля {sorted(required-set(manifest))}')
    require(manifest['schema_version'] == SCHEMA_VERSION, f'{label}: несовместимая schema_version; ожидается {SCHEMA_VERSION}')
    uuid_text(manifest['run_id'])
    require(manifest['status'] == ('candidate' if candidate else 'complete'), f'{label}: неверный status')
    for key, names in [('input_sha256', INPUT_SCHEMAS), ('output_sha256', OUTPUT_SCHEMAS)]:
        hashes = manifest[key]
        require(isinstance(hashes, dict) and set(names) <= set(hashes), f'{label}: неверный {key}')
        require(all(isinstance(hashes[n], str) and re.fullmatch('[0-9a-f]{64}', hashes[n]) for n in names), f'{label}: неверный SHA256 в {key}')
    counts = manifest['counts']
    require(isinstance(counts, dict) and set(Counts.__annotations__) <= set(counts), f'{label}: неверные counts')
    require(all(type(counts[k]) is int and counts[k] >= 0 for k in Counts.__annotations__), f'{label}: counts должны быть неотрицательными целыми')
    require(counts['nodes'] > 0, f'{label}: counts.nodes должен быть >0')
    require(manifest['seed'] == RANDOM_SEED, f'{label}: неверный seed')
    versions = manifest['versions']
    version_keys = ('python', 'numpy', 'pandas', 'pyarrow', 'networkx', 'scipy', 'methodology')
    require(isinstance(versions, dict) and all(isinstance(versions.get(k), str) and versions[k].strip() for k in version_keys), f'{label}: неполные versions')
    thresholds = manifest['thresholds']
    require(isinstance(thresholds, dict) and set(Thresholds.__annotations__) <= set(thresholds), f'{label}: неполные thresholds')
    for key in Thresholds.__annotations__:
        value = thresholds[key]
        require(value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0), f'{label}: неверный threshold {key}')
        if key in ('in_degree_min', 'out_degree_min') and value is not None:
            require(type(value) is int, f'{label}: {key} должен быть целым')
    scales = manifest['normalization_scales']
    scale_names = ('in_deg','out_deg','in_kzt','out_kzt','in_tx','out_tx','betweenness','pagerank','cross_cluster_degree','turnover_kzt')
    require(isinstance(scales, dict) and all(type(scales.get(k)) in (int, float) and math.isfinite(scales[k]) and scales[k] >= 0 for k in scale_names), f'{label}: неверные normalization_scales')
    require(manifest['priority_weights'] == PRIORITY_WEIGHTS, f'{label}: priority_weights не соответствуют контракту')
    times = manifest['stage_runtimes_seconds']
    require(isinstance(times, dict) and all(type(times.get(k)) in (int, float) and math.isfinite(times[k]) and times[k] >= 0 for k in ('load','features','roles','export','validation','total')), f'{label}: неверные stage_runtimes_seconds')
    if not candidate:
        require(times['total'] < 300, f'{label}: runtime должен быть <300 секунд')
        validation = manifest['validation']
        require(isinstance(validation, dict) and validation.get('status') == 'passed' and isinstance(validation.get('validator_version'), str) and validation['validator_version'].strip(), f'{label}: validation не подтверждена')
    distribution = manifest['role_distribution']
    require(isinstance(distribution, dict) and set(distribution) == set(ROLES) and all(type(v) is int and v >= 0 for v in distribution.values()), f'{label}: неверное role_distribution')
    require(isinstance(manifest['warnings'], list) and all(isinstance(w, str) for w in manifest['warnings']), f'{label}: warnings должен быть списком строк')


def read_bundle_files(root: Path, candidate=False):
    directory, pointer_id = resolve_run(root, candidate)
    name = 'candidate.json' if candidate else 'run.json'
    raw_manifest = (directory/name).read_bytes()
    manifest = json_object(raw_manifest, name)
    check_manifest(manifest, candidate)
    if pointer_id is not None:
        require(manifest['run_id'] == pointer_id, 'current.json: run_id не совпадает с manifest')
    if directory.parent.name in ('runs', '.staging'):
        require(directory.name == manifest['run_id'], 'Имя каталога запуска не совпадает с run_id')
    raw = {}
    for filename in OUTPUT_SCHEMAS:
        raw[filename] = (directory/filename).read_bytes()
        require(sha256(raw[filename]).hexdigest() == manifest['output_sha256'][filename], f'{filename}: SHA256 не совпадает; смешанные/повреждённые результаты')
    require((directory/name).read_bytes() == raw_manifest, f'{name} изменился во время чтения')
    return directory, manifest, raw
