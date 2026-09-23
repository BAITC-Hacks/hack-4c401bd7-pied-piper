"""Durable polling worker. Run separately from the HTTP process."""
import argparse
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import threading
import time
from uuid import uuid4

from backend.config import Settings
from backend.datasets import safe_validation_detail, validate_dataset
from backend.errors import error_body
from backend.store import Store, now, timestamp
from pipeline import run_pipeline
from src.results import load_bundle

LOG = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.store = Store(settings.state)
        self.owner = str(uuid4())

    @contextmanager
    def beating(self, table, identifier):
        stop = threading.Event()

        def beat():
            while not stop.wait(2):
                try:
                    if not self.store.heartbeat(table, identifier, self.owner):
                        return
                except Exception:
                    LOG.exception("Heartbeat failed for %s", identifier)

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    def finish_run(self, identifier, status, error=None, bundle_path=None):
        doc = self.store.get('runs', identifier)['document']
        self.store.update_owned('runs', identifier, self.owner,
                                dict(status=status, stage=None, finished_at=now(), error=error,
                                     elapsed_seconds=max(0, time.time() - timestamp(doc['created_at']))), bundle_path)

    def recover(self):
        cutoff = time.time() - self.settings.lease_seconds
        for table, identifier, old_owner in self.store.stale(cutoff):
            if not self.store.take_stale(table, identifier, old_owner, cutoff, self.owner):
                continue
            if table == 'runs':
                path = self.settings.results / 'runs' / identifier
                try:
                    bundle = load_bundle(path)
                    record = self.store.get('runs', identifier)
                    dataset = self.store.get('datasets', record['dataset_id'])
                    if bundle.manifest['run_id'] != identifier or bundle.manifest['input_sha256'] != json.loads(dataset['hashes']):
                        raise ValueError('Recovered bundle mismatch')
                except (OSError, ValueError):
                    self.finish_run(identifier, 'failed', error_body('INTERNAL_ERROR', 'Worker остановился до завершения. Запустите расчёт заново.'))
                else:
                    self.finish_run(identifier, 'succeeded', bundle_path=path)
            else:
                self.store.update_owned(table, identifier, self.owner,
                                        dict(status='failed', failure=error_body('INTERNAL_ERROR', 'Проверка прервана. Загрузите набор заново.')))

    def once(self):
        self.recover()
        dataset = self.store.claim('datasets', self.owner)
        if dataset:
            identifier = dataset['id']
            with self.beating('datasets', identifier):
                try:
                    result = validate_dataset(Path(dataset['path']), dataset['expected_nodes'],
                                              json.loads(dataset['hashes']), dataset['document']['data_kind'])
                except ValueError as exc:
                    result = dict(status='invalid', validation=dict(errors=[safe_validation_detail(exc, dataset['path'])], warnings=[]))
                except Exception:
                    LOG.exception('Dataset validation failed: %s', identifier)
                    result = dict(status='failed', failure=error_body('INTERNAL_ERROR', 'Не удалось проверить набор. Повторите импорт.'))
                self.store.update_owned('datasets', identifier, self.owner, result)
            return True
        run = self.store.claim('runs', self.owner)
        if run is None:
            return False
        identifier = run['id']
        dataset = self.store.get('datasets', run['dataset_id'])
        with self.beating('runs', identifier):
            try:
                # Check immutable inputs again; never calculate from a changed snapshot.
                validate_dataset(Path(dataset['path']), dataset['expected_nodes'],
                                 json.loads(dataset['hashes']), dataset['document']['data_kind'])
                path = run_pipeline(Path(dataset['path']), self.settings.results, dataset['expected_nodes'],
                                    run_id=identifier,
                                    on_stage=lambda stage: self.store.update_owned('runs', identifier, self.owner, dict(stage=stage)),
                                    publication_guard=lambda: self.store.publication_guard(identifier, self.owner))
                load_bundle(path)
                self.finish_run(identifier, 'succeeded', bundle_path=path)
            except Exception:
                LOG.exception('Run failed: %s', identifier)
                try:
                    self.finish_run(identifier, 'failed', error_body('INTERNAL_ERROR', 'Расчёт не завершён. Проверьте журнал worker и повторите запуск.'))
                except RuntimeError:
                    LOG.warning('Lease already recovered for %s', identifier)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Process at most one task and exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = Worker(Settings.from_env())
    while True:
        worked = worker.once()
        if args.once:
            return
        if not worked:
            time.sleep(1)


if __name__ == '__main__':
    main()
