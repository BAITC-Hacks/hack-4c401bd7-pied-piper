"""FastAPI endpoints for the local Money Graph workspace."""
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import shutil
import tempfile
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool

from backend import models as m
from backend.analytics import Analysis, page
from backend.config import Settings
from backend.datasets import bootstrap, dataset_document, hashes
from backend.errors import ApiProblem, detail, error_body
from backend.store import Store, timestamp
from src.contracts import CSV_OPTIONS, NODES_ROLES_SCHEMA, column_names

LOG = logging.getLogger(__name__)
UPLOAD_SCHEMA = {
    'type': 'object', 'required': ['name', 'profile', 'nodes', 'edges', 'transactions'],
    'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        'profile': {'type': 'string', 'enum': ['hackathon-v1']},
        **{key: {'type': 'string', 'format': 'binary'} for key in ('nodes', 'edges', 'transactions')},
    },
}


class BoundaryMiddleware:
    """Bound even chunked uploads before multipart parsing and add request IDs."""
    def __init__(self, app, upload_limit):
        self.app, self.upload_limit = app, upload_limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault('state', {})['request_id'] = request_id
        started = False

        async def tagged(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message['headers'] = [*message.get('headers', []), (b'x-request-id', request_id.encode())]
            await send(message)

        limit = self.upload_limit if scope['path'] == '/api/v1/datasets' else 1024 * 1024
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body:
            total = 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                total += len(chunk)
                if total > limit:
                    response = JSONResponse(dict(error=error_body('UPLOAD_TOO_LARGE', 'Превышен допустимый размер запроса.'), request_id=request_id), status_code=413)
                    return await response(scope, receive, tagged)
                body.write(chunk)
                if not message.get('more_body', False):
                    break
            body.seek(0)
            exhausted = False

            async def replay():
                nonlocal exhausted
                if exhausted:
                    return await receive()
                chunk = body.read(1024 * 1024)
                exhausted = body.tell() == total
                return dict(type='http.request', body=chunk, more_body=not exhausted)

            try:
                await self.app(scope, replay, tagged)
            except Exception:
                LOG.exception('Unhandled request %s', request_id)
                if not started:
                    response = JSONResponse(dict(error=error_body('INTERNAL_ERROR', 'Внутренняя ошибка сервера.'), request_id=request_id), status_code=500)
                    await response(scope, receive, tagged)


async def query_contract(request: Request):
    fields = request.scope['route'].dependant.query_params
    allowed = set()
    for field in fields:
        annotation = field.field_info.annotation
        allowed.update(annotation.model_fields if hasattr(annotation, 'model_fields') else [field.alias])
    if set(request.query_params) - allowed:
        raise ApiProblem(422, 'INVALID_REQUEST', 'Неизвестный параметр запроса.')
    for key in request.query_params:
        if key != 'role' and len(request.query_params.getlist(key)) > 1:
            raise ApiProblem(422, 'INVALID_REQUEST', 'Параметр запроса не должен повторяться.')


def current_run(doc):
    doc = dict(doc)
    if doc['status'] in ('queued', 'running'):
        doc['elapsed_seconds'] = max(0, time.time() - timestamp(doc['created_at']))
    return doc


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    store = Store(settings.state)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(bootstrap, settings, store)
        yield

    app = FastAPI(title='Money Graph API', version='1.0.0', lifespan=lifespan,
                  dependencies=[Depends(query_contract)],
                  responses={status: {'model': m.ApiError} for status in (404, 409, 413, 415, 422, 500, 503)})
    app.state.store = store
    app.state.settings = settings
    app.add_middleware(BoundaryMiddleware, upload_limit=settings.body_limit)

    @app.exception_handler(ApiProblem)
    async def problem(request, exc):
        return JSONResponse(dict(error=exc.body, request_id=request.state.request_id), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        details = [detail(e['msg'], field='.'.join(str(v) for v in e['loc']), code=e['type']) for e in exc.errors()]
        return await problem(request, ApiProblem(422, 'INVALID_REQUEST', 'Некорректные параметры запроса.', details=details))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        status = 422 if exc.status_code == 400 else exc.status_code
        return await problem(request, ApiProblem(status, 'NOT_FOUND' if status == 404 else 'INVALID_REQUEST',
                                                 'Ресурс не найден.' if exc.status_code == 404 else 'Запрос не поддерживается.'))

    def analysis(run_id):
        return Analysis(store, run_id)

    prefix = '/api/v1'

    @app.get(prefix + '/health')
    def health():
        with store.connect() as db:
            db.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get(prefix + '/datasets', response_model=m.Page[m.Dataset])
    def datasets(query: Annotated[m.Paging, Query()]):
        return store.list('datasets', query.limit, query.offset)

    @app.get(prefix + '/datasets/{dataset_id}', response_model=m.Dataset)
    def dataset(dataset_id: m.Identifier):
        return store.get('datasets', dataset_id)['document']

    @app.post(prefix + '/datasets', response_model=m.Dataset, status_code=202,
              openapi_extra={'requestBody': {'required': True, 'content': {
                  'multipart/form-data': {'schema': UPLOAD_SCHEMA}}}})
    async def upload(request: Request, response: Response):
        if not request.headers.get('content-type', '').startswith('multipart/form-data'):
            raise ApiProblem(415, 'UNSUPPORTED_FILE_TYPE', 'Ожидается multipart/form-data.')
        async with request.form(max_files=3, max_fields=2) as form:
            required = {'name', 'profile', 'nodes', 'edges', 'transactions'}
            if set(form) != required or any(len(form.getlist(k)) != 1 for k in required):
                raise ApiProblem(422, 'INVALID_REQUEST', 'Нужны name, profile и ровно три файла.')
            name = form['name']
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or form['profile'] != 'hackathon-v1':
                raise ApiProblem(422, 'INVALID_REQUEST', 'Неверное имя или профиль набора.')
            for key in ('nodes', 'edges', 'transactions'):
                value = form[key]
                if not isinstance(value, UploadFile) or not (value.filename or '').lower().endswith('.parquet'):
                    raise ApiProblem(415, 'UNSUPPORTED_FILE_TYPE', 'Ожидаются три Parquet.', details=[detail('Требуется Parquet.', file=key)])
                if value.size is not None and value.size > settings.file_limit:
                    raise ApiProblem(413, 'UPLOAD_TOO_LARGE', 'Файл превышает 50 MiB.', details=[detail('Превышен размер файла.', file=key)])
            # Imports must match the known official profile. Trust derives from the
            # configured dataset, never from a client-supplied flag or row count.
            trusted_hashes = await run_in_threadpool(hashes, settings.data)
            identifier = str(uuid4())
            directory = settings.state / 'datasets' / identifier
            directory.mkdir(parents=True, exist_ok=False)
            try:
                for key in ('nodes', 'edges', 'transactions'):
                    length = 0
                    with (directory / f'{key}.parquet').open('wb') as target:
                        while chunk := await form[key].read(1024 * 1024):
                            length += len(chunk)
                            if length > settings.file_limit:
                                raise ApiProblem(413, 'UPLOAD_TOO_LARGE', 'Файл превышает 50 MiB.')
                            target.write(chunk)
                doc = dataset_document(identifier, name.strip())
                await run_in_threadpool(store.add_dataset, doc, directory, 2248, trusted_hashes)
            except BaseException:
                # directory is an internally generated UUID child, never a user path.
                if directory.resolve().parent == (settings.state / 'datasets').resolve():
                    shutil.rmtree(directory)
                raise
        response.headers['Location'] = f'{prefix}/datasets/{identifier}'
        return doc

    @app.get(prefix + '/runs', response_model=m.Page[m.Run])
    def runs(query: Annotated[m.RunQuery, Query()]):
        store.get('datasets', query.dataset_id)
        result = store.list('runs', query.limit, query.offset, query.dataset_id)
        result['items'] = [current_run(r) for r in result['items']]
        return result

    @app.post(prefix + '/runs', response_model=m.Run, status_code=202)
    def create_run(body: m.CreateRun, response: Response,
                   idempotency_key: Annotated[m.Identifier, Header()]):
        run = store.create_run(body.dataset_id, idempotency_key)
        response.headers['Location'] = f'{prefix}/runs/{run["run_id"]}'
        return current_run(run)

    @app.get(prefix + '/runs/{run_id}', response_model=m.Run)
    def run(run_id: m.Identifier):
        return current_run(store.get('runs', run_id)['document'])

    @app.get(prefix + '/runs/{run_id}/summary', response_model=m.Summary)
    def summary(run_id: m.Identifier):
        return analysis(run_id).summary()

    @app.get(prefix + '/runs/{run_id}/nodes', response_model=m.RunPage[m.NodeSummary])
    def nodes(run_id: m.Identifier, query: Annotated[m.NodeQuery, Query()]):
        return analysis(run_id).list_nodes(query)

    @app.get(prefix + '/runs/{run_id}/nodes/{gid}', response_model=m.NodeDetail)
    def node(run_id: m.Identifier, gid: m.Gid):
        return analysis(run_id).detail(gid)

    @app.get(prefix + '/runs/{run_id}/nodes/{gid}/edges', response_model=m.RunPage[m.Edge])
    def edges(run_id: m.Identifier, gid: m.Gid, query: Annotated[m.EdgeQuery, Query()]):
        return analysis(run_id).edges(gid, query)

    @app.get(prefix + '/runs/{run_id}/nodes/{gid}/graph', response_model=m.Graph)
    def graph(run_id: m.Identifier, gid: m.Gid, query: Annotated[m.GraphQuery, Query()]):
        return analysis(run_id).graph(gid, query)

    @app.get(prefix + '/runs/{run_id}/top', response_model=m.RunPage[m.RankedNode])
    def top(run_id: m.Identifier, query: Annotated[m.TopQuery, Query()]):
        return analysis(run_id).top(query)

    @app.get(prefix + '/runs/{run_id}/clusters', response_model=m.RunPage[m.Cluster])
    def clusters(run_id: m.Identifier, query: Annotated[m.Paging, Query()]):
        return page(analysis(run_id).clusters(), query, run_id)

    @app.get(prefix + '/runs/{run_id}/clusters/{cluster_id}', response_model=m.ClusterDetail)
    def cluster(run_id: m.Identifier, cluster_id: int):
        if cluster_id < 0:
            raise ApiProblem(422, 'INVALID_REQUEST', 'cluster_id должен быть неотрицательным.')
        item = next((r for r in analysis(run_id).clusters() if r['cluster_id'] == cluster_id), None)
        if item is None:
            raise ApiProblem(404, 'NOT_FOUND', 'Кластер не найден.')
        return dict(**item, run_id=run_id)

    @app.get(prefix + '/runs/{run_id}/cluster-graph', response_model=m.ClusterGraph)
    def cluster_graph(run_id: m.Identifier, query: Annotated[m.GraphLimits, Query()]):
        return analysis(run_id).cluster_graph(query)

    @app.get(prefix + '/runs/{run_id}/selection', response_model=m.Selection)
    def selection(run_id: m.Identifier):
        analysis(run_id)
        return store.selection(run_id)

    @app.put(prefix + '/runs/{run_id}/selection', response_model=m.Selection)
    def put_selection(run_id: m.Identifier, body: m.PutSelection):
        current = analysis(run_id)
        gids = list(dict.fromkeys(body.gids))
        if len(gids) > 500:
            raise ApiProblem(422, 'SELECTION_LIMIT', 'Допускается не более 500 клиентов.')
        if set(gids) - set(current.nodes):
            raise ApiProblem(422, 'INVALID_REQUEST', 'Выбран неизвестный клиент.')
        return store.put_selection(run_id, gids)

    def csv_response(raw, filename):
        return Response(raw, media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get(prefix + '/runs/{run_id}/selection/export', response_class=Response,
             responses={200: {'content': {'text/csv': {'schema': {'type': 'string'}}}}})
    def selection_export(run_id: m.Identifier):
        current = analysis(run_id)
        selected = store.selection(run_id)['gids']
        frame = current.bundle.nodes.set_index('gid', drop=False).loc[selected, list(column_names(NODES_ROLES_SCHEMA))]
        return csv_response(frame.to_csv(**{k: v for k, v in CSV_OPTIONS.items() if k != 'encoding'}).encode('utf-8'), 'selected_nodes.csv')

    @app.get(prefix + '/runs/{run_id}/exports/{filename}', response_class=Response,
             responses={200: {'content': {'text/csv': {'schema': {'type': 'string'}}}}})
    def export(run_id: m.Identifier, filename: Literal['nodes_roles.csv', 'clusters.csv', 'top_nodes.csv']):
        return csv_response(analysis(run_id).bundle.raw[filename], filename)

    return app
