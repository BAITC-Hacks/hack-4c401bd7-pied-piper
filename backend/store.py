"""SQLite registry with transactional claims, idempotency and durable selection."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import time
from uuid import uuid4

from backend.errors import ApiProblem, error_body


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "registry.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported registry schema version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, path TEXT NOT NULL,
                    expected_nodes INTEGER NOT NULL, hashes TEXT NOT NULL,
                    owner TEXT, heartbeat REAL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
                    document TEXT NOT NULL, status TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE, bundle_path TEXT,
                    owner TEXT, heartbeat REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(dataset_id)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS selections (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    gids TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, table, identifier):
        if table not in ("datasets", "runs"):
            raise ValueError("Invalid registry table")
        with self.connect() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApiProblem(404, "NOT_FOUND", "Ресурс не найден.")
        result = dict(row)
        result["document"] = json.loads(row["document"])
        return result

    def list(self, table, limit, offset, dataset_id=None):
        if table not in ("datasets", "runs"):
            raise ValueError("Invalid registry table")
        clause = " WHERE dataset_id=?" if table == "runs" else ""
        args = (dataset_id,) if table == "runs" else ()
        with self.connect() as db:
            total = db.execute(f"SELECT count(*) FROM {table}{clause}", args).fetchone()[0]
            rows = db.execute(
                f"SELECT document FROM {table}{clause} "
                "ORDER BY json_extract(document, '$.created_at') DESC, id ASC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return dict(items=[json.loads(r[0]) for r in rows], total=total, limit=limit, offset=offset)

    def add_dataset(self, document, path, expected_nodes, hashes):
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO datasets(id,document,path,expected_nodes,hashes) VALUES(?,?,?,?,?)",
                       (document["dataset_id"], json.dumps(document), str(path), expected_nodes, json.dumps(hashes)))

    def create_run(self, dataset_id, key):
        with self.connect(write=True) as db:
            old = db.execute("SELECT * FROM runs WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if old["dataset_id"] != dataset_id:
                    raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Ключ уже использован для другого запроса.")
                return json.loads(old["document"])
            dataset = db.execute("SELECT document FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if dataset is None:
                raise ApiProblem(404, "NOT_FOUND", "Набор не найден.")
            if json.loads(dataset[0])["status"] != "ready":
                raise ApiProblem(409, "DATASET_NOT_READY", "Набор ещё не прошёл проверку.")
            active = db.execute("SELECT id FROM runs WHERE dataset_id=? AND status IN ('queued','running')",
                                (dataset_id,)).fetchone()
            if active:
                raise ApiProblem(409, "ACTIVE_RUN_EXISTS", "Для набора уже выполняется расчёт.",
                                 related_run_id=active[0])
            doc = dict(run_id=str(uuid4()), dataset_id=dataset_id, status="queued", stage=None,
                       created_at=now(), started_at=None, finished_at=None, elapsed_seconds=0.0,
                       methodology_version="lane-a-v1", error=None)
            db.execute("INSERT INTO runs(id,dataset_id,document,status,idempotency_key) VALUES(?,?,?,?,?)",
                       (doc["run_id"], dataset_id, json.dumps(doc), "queued", key))
        return doc

    def register_run(self, doc, bundle_path):
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO runs(id,dataset_id,document,status,bundle_path) VALUES(?,?,?,?,?)",
                       (doc["run_id"], doc["dataset_id"], json.dumps(doc), doc["status"], str(bundle_path)))

    def claim(self, table, owner):
        if table not in ("datasets", "runs"):
            raise ValueError("Invalid registry table")
        condition = ("json_extract(document,'$.status')='validating' AND owner IS NULL"
                     if table == "datasets" else "status='queued'")
        with self.connect(write=True) as db:
            row = db.execute(f"SELECT * FROM {table} WHERE {condition} ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            doc = json.loads(row["document"])
            if table == "runs":
                doc.update(status="running", stage="load", started_at=now())
                db.execute("UPDATE runs SET status='running' WHERE id=?", (row["id"],))
            db.execute(f"UPDATE {table} SET document=?,owner=?,heartbeat=? WHERE id=?",
                       (json.dumps(doc), owner, time.time(), row["id"]))
        return self.get(table, row["id"])

    def heartbeat(self, table, identifier, owner):
        with self.connect(write=True) as db:
            changed = db.execute(f"UPDATE {table} SET heartbeat=? WHERE id=? AND owner=?",
                                 (time.time(), identifier, owner)).rowcount
        return changed == 1

    def update_owned(self, table, identifier, owner, changes, bundle_path=None):
        with self.connect(write=True) as db:
            row = db.execute(f"SELECT document FROM {table} WHERE id=? AND owner=?", (identifier, owner)).fetchone()
            if row is None:
                raise RuntimeError("Worker lease lost")
            doc = json.loads(row[0])
            doc.update(changes)
            terminal = doc["status"] in ("ready", "invalid", "failed", "succeeded")
            if table == "runs":
                db.execute("UPDATE runs SET status=?,bundle_path=COALESCE(?,bundle_path) WHERE id=?",
                           (doc["status"], str(bundle_path) if bundle_path else None, identifier))
            db.execute(f"UPDATE {table} SET document=?,owner=?,heartbeat=? WHERE id=?",
                       (json.dumps(doc, allow_nan=False), None if terminal else owner,
                        None if terminal else time.time(), identifier))

    @contextmanager
    def publication_guard(self, identifier, owner):
        # Keep recovery and the final file rename mutually exclusive.
        with self.connect(write=True) as db:
            row = db.execute("SELECT 1 FROM runs WHERE id=? AND owner=? AND status='running'",
                             (identifier, owner)).fetchone()
            if not row:
                raise RuntimeError("Worker lease lost")
            yield

    def stale(self, cutoff):
        result = []
        with self.connect() as db:
            for table in ("datasets", "runs"):
                rows = db.execute(f"SELECT id,owner FROM {table} WHERE owner IS NOT NULL AND heartbeat < ?",
                                  (cutoff,)).fetchall()
                result.extend((table, r["id"], r["owner"]) for r in rows)
        return result

    def take_stale(self, table, identifier, old_owner, cutoff, owner):
        with self.connect(write=True) as db:
            return db.execute(f"UPDATE {table} SET owner=?,heartbeat=? WHERE id=? AND owner=? AND heartbeat < ?",
                              (owner, time.time(), identifier, old_owner, cutoff)).rowcount == 1

    def selection(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM selections WHERE run_id=?", (run_id,)).fetchone()
        return dict(run_id=run_id, gids=json.loads(row["gids"]) if row else [],
                    updated_at=row["updated_at"] if row else None)

    def put_selection(self, run_id, gids):
        updated = now()
        with self.connect(write=True) as db:
            db.execute("INSERT INTO selections VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET gids=excluded.gids,updated_at=excluded.updated_at",
                       (run_id, json.dumps(gids), updated))
        return dict(run_id=run_id, gids=gids, updated_at=updated)
