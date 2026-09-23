# Money Graph

Локальный инструмент объяснимой AML-аналитики графа переводов команды Pied Piper.
Роли и приоритеты — сигналы для ручной проверки, а не доказательства нарушения.

## Структура

```text
backend/             API, worker, pipeline и независимый validator
  core/              загрузка, контракты, графовая аналитика, экспорт и чтение результатов
frontend/            Streamlit, визуализация графа и HTML-шаблон
docs/                архитектура, методология, контракты и отчёты аудита
tests/               аналитические, API, UI и интеграционные проверки
scripts/             служебные команды запуска и проверки
data/                локальные исходные Parquet (не входят в Git)
outputs/             локальные результаты расчёта (не входят в Git)
.streamlit/          настройки Streamlit для запуска из корня проекта
Dockerfile           общий runtime-образ и отдельная стадия tests
compose.yaml         UI, подготовка результатов, API, worker и CLI
requirements.lock    закреплённые зависимости общего Python-окружения
```

Все команды выполняются из корня репозитория. Используется Python 3.12.
UI читает опубликованные файлы напрямую; API и worker работают отдельным сервисом.

## Локальный запуск

Windows PowerShell (нужен `uv`):

```powershell
.\make.cmd setup
.\make.cmd demo
```

Синтетический демонабор создаётся локально и не требует исходных данных.
Для расчёта предоставленного набора разместите `nodes.parquet`, `edges.parquet`
и `transactions.parquet` в `data/`, затем:

```powershell
.\make.cmd run
```

Linux/macOS: `make setup`, затем `make demo` или `make run`.
Интерфейс: http://localhost:3000. Для существующих результатов — `make ui` / `.\make.cmd ui`.
API и worker: `make backend` / `.\make.cmd backend` (порт 8000).

Прямые команды после активации окружения:

```bash
python -m backend.pipeline --data data --out outputs
python -m backend.validate --data data --out outputs
python -m streamlit run frontend/app.py --server.port 3000 -- --outputs outputs
python -m backend
python -m pytest -q
```

## Docker

Для UI нужны три входных Parquet в `data/`:

```bash
docker compose build ui
docker compose up -d --no-build --pull never
```

`prepare` рассчитывает результаты в именованном volume `results` либо проверяет
существующую публикацию. `ui` запускается после успешной проверки на http://localhost:3000.

```bash
docker compose run --rm validate
docker compose run --rm --no-deps pipeline
docker compose --profile backend up -d api worker
docker compose build tests
docker compose run --rm tests
docker compose down
```

API доступен на http://localhost:8000. API/worker используют отдельный volume
`backend-state`, UI — `results`. Локальный `outputs/` не является Docker volume.
Остановка через `down` сохраняет volumes. Данные и результаты не включаются в образ.

## Аудит и документация

- [Навигация по документации](docs/README.md)
- [Подробное руководство](docs/product-guide.md)
- [Методология](docs/methodology.md) и [архитектура](docs/architecture.md)
- [Backend API и ограничения](docs/backend.md)
- [Воспроизводимые проверки](docs/qa.md)
- [Схема исходных данных](data/README.md)

Исходные данные, результаты, окружения, runtime и кэши исключены из Git.
На чистом клоне исходные данные предоставляются отдельно; тесты реальных данных
требуют локального набора. Исторические отчёты описывают проверки указанных в них
версий и не заменяют новый прогон. Ранее закоммиченные данные остаются в истории Git.
