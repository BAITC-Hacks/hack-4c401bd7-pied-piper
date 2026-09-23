"""Import profile validation and explicit registration of existing snapshots."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import uuid5, NAMESPACE_URL

import pandas as pd

from backend.store import now
from backend.errors import detail
from src.contracts import INPUT_SCHEMAS
from src.data import load_data
from src.results import load_bundle
from validate import check_inputs


def hashes(directory):
    return {name: sha256((Path(directory) / name).read_bytes()).hexdigest() for name in INPUT_SCHEMAS}


def dataset_document(identifier, name, kind="official"):
    return dict(dataset_id=identifier, name=name, status="validating", profile="hackathon-v1",
                data_kind=kind, created_at=now(), counts=None, period=None,
                collection=dict(max_depth=4, min_amount_kzt=5000, direction="outgoing"),
                validation=dict(errors=[], warnings=[]), failure=None)


def validate_dataset(directory, expected_nodes, expected_hashes, kind):
    nodes, edges, transactions, _ = load_data(Path(directory), expected_nodes=expected_nodes)
    if hashes(directory) != expected_hashes:
        raise ValueError("Файлы не совпадают с зарегистрированным набором профиля hackathon-v1.")
    if kind == "official" and (len(nodes), len(edges), len(transactions), int(nodes.is_seed.sum())) != (2248, 3119, 4840, 81):
        raise ValueError("Число записей не соответствует официальному набору.")
    return dict(status="ready", counts=dict(nodes=len(nodes), edges=len(edges), transactions=len(transactions),
                                           seeds=int(nodes.is_seed.sum())),
                period=dict(**{"from": transactions.date.min().date().isoformat(),
                               "to": transactions.date.max().date().isoformat()}),
                validation=dict(errors=[], warnings=(["Синтетический пример."] if kind == "synthetic" else [])),
                failure=None)


def safe_validation_detail(exc, directory):
    message = str(exc).replace(str(directory), "[набор]")
    # Loader messages have known schema labels; do not return arbitrary reader traces.
    if len(message) > 400 or "Traceback" in message:
        message = "Не удалось проверить содержимое Parquet. Проверьте схему и значения."
    file = next((name for name in INPUT_SCHEMAS if name in message), None)
    if file is None:
        file = next((name for name in INPUT_SCHEMAS if name.split('.')[0] + ':' in message), None)
    fields = [f.name for schema in INPUT_SCHEMAS.values() for f in schema]
    field = next((f for f in fields if f in message.split()), None)
    return detail(message, file=file, field=field)


def bootstrap(settings, store):
    """Register immutable copies once; verify stored copies on subsequent starts."""
    if not settings.bootstrap:
        return
    source_hashes = hashes(settings.data)
    identifier = str(uuid5(NAMESPACE_URL, "money-graph:" + json.dumps(source_hashes, sort_keys=True)))
    existing = None
    try:
        existing = load_bundle(settings.outputs)
    except (OSError, ValueError):
        # No current pointer is valid for a first start; a damaged publication is not.
        if (settings.outputs / 'current.json').exists() or (settings.outputs / 'run.json').exists():
            raise
    kind = "synthetic" if existing and existing.manifest.get("data_kind") == "synthetic" else "official"
    expected = existing.manifest["counts"]["nodes"] if existing else 2248
    if existing:
        check_inputs(existing, settings.data, expected)
    directory = settings.state / "datasets" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    for name in INPUT_SCHEMAS:
        destination = directory / name
        if not destination.exists():
            shutil.copyfile(settings.data / name, destination)
    doc = dataset_document(identifier, "Официальный набор" if kind == "official" else "Синтетический пример", kind)
    doc.update(validate_dataset(directory, expected, source_hashes, kind))
    store.add_dataset(doc, directory, expected, source_hashes)
    if existing:
        run_id = existing.manifest["run_id"]
        # Backend owns a snapshot; later CLI publication cannot change API history.
        target = settings.results / "runs" / run_id
        target.mkdir(parents=True, exist_ok=True)
        for name, raw in existing.raw.items():
            if not (target / name).exists():
                (target / name).write_bytes(raw)
        manifest = target / "run.json"
        if not manifest.exists():
            manifest.write_text(json.dumps(existing.manifest, ensure_ascii=False), encoding="utf-8")
        load_bundle(target)
        moment = now()
        store.register_run(dict(run_id=run_id, dataset_id=identifier, status="succeeded", stage=None,
                                created_at=moment, started_at=moment, finished_at=moment,
                                elapsed_seconds=existing.manifest["stage_runtimes_seconds"]["total"],
                                methodology_version=existing.manifest["versions"]["methodology"], error=None), target)
