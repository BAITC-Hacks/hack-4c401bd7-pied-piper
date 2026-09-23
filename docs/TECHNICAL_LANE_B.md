# UI и validator — команды и контракт v1.0.0

Источник схем — [backend/core/contracts.py](../backend/core/contracts.py). Состояние продукта и основная установка описаны в [README](../README.md). Эта инструкция относится к существующему Streamlit UI; отдельные [HTTP API и worker](backend.md) уже реализованы, но Streamlit к ним не подключён. Новый frontend пока не реализован.

## Запуск на Windows

Из корня проекта, при установленном uv:

```powershell
.\make.cmd setup
.\make.cmd run
```

`run` выполняет расчёт, проверку и запускает UI на http://localhost:3000. Чтобы открыть уже рассчитанный результат:

```powershell
.\make.cmd validate
.\make.cmd ui
```

В Linux/macOS эквиваленты — `make setup`, `make run`, `make validate`, `make ui`. Остановка локального UI — Ctrl+C или `make stop` / `.\make.cmd stop`.

## Docker

```bash
docker compose build ui
docker compose up -d --no-build --pull never
docker compose run --rm validate
```

UI — http://localhost:3000; результаты в отдельном volume `money-graph_results`. При первом старте `prepare` считает данные; при повторном проверяет сохранённый результат. Для обновления после изменения данных или аналитики:

```bash
docker compose run --rm --no-deps pipeline
```

После изменения кода сначала пересобрать образ. Остановка — `docker compose down`, данные volume сохраняются.

## Формат публикации

`outputs/current.json` содержит `schema_version: "1.0.0"` и UUID запуска. Каталог `outputs/runs/<UUID>/` содержит три CSV, `node_metrics.parquet`, `edges.parquet`, `run.json`. Прямой путь к каталогу запуска также поддерживается.

Manifest фиксирует status=complete, validation.status=passed, версии, SHA256 входа/выхода, counts, seed=42, пороги, normalization scales, веса, времена этапов, распределение ролей и warnings. UI не читает исходный data/ и не пересчитывает роли. В пределах отображаемого результата используются файлы одного запуска.

### Проверка до публикации

1. Pipeline пишет пять файлов и `candidate.json` в `outputs/.staging/<UUID>/`. Candidate содержит все поля manifest, кроме validation, со status=candidate.
2. Вызывает validator:

```powershell
.\.venv\Scripts\python.exe -m backend.validate --data data --out outputs/.staging/<UUID> --candidate
```

В реальной команде заменить `<UUID>` идентификатором каталога. Linux/macOS: `.venv/bin/python`.

При успехе stdout — один JSON `{"status":"passed","validator_version":"1.0.0"}`; подробности идут в stderr. При ошибке exit=1. Validator не изменяет файлы и не публикует candidate.

3. Pipeline фиксирует время, удаляет candidate.json, пишет run.json, перемещает каталог в runs/ и атомарно меняет current.json. Если total ≥300 секунд, публикация отклоняется.
4. Повреждённый результат UI не подменяет старым: показывает ошибку.

## Проверки

```powershell
.\make.cmd test
.\make.cmd validate
.\.venv\Scripts\python.exe scripts/audit_evidence.py --data data --out outputs
```

Validator проверяет типы/порядок колонок, int64 без потери точности, nullable-поля, покрытие узлов, Top, кластерные суммы, базовые метрики, ограничения seed/depth, арифметику ролей и вкладов, manifest и хеши. PageRank/betweenness не пересчитывает и качество AML-гипотез не доказывает. Отдельный audit_evidence сверяет числовой текст с исходными рёбрами, оговорки и why.

Синтетическая тренировка — `.\make.cmd demo` / `make demo`; используются отдельные demo_data/ и demo_outputs/, в интерфейсе постоянная пометка.

## Интерфейс

- «Проверка клиентов»: первые восемь клиентов очереди, список остальных, точный поиск, глобальное место, смысл роли, evidence, приоритет и рекомендации по проверке.
- Поиск работает независимо от фильтров. В UI приоритет показан в шкале 0–100; CSV хранит [0,1]. Альтернатива, margin и все признаки доступны по раскрытию.
- Локальный SVG-граф: перетаскивание, pan/zoom, подсветка соседей, подписи, reset и две раскладки. Ego по умолчанию 40 узлов, выбор 20/40/60/100, максимум 350 рёбер; полные прямые связи в таблицах.
- «Сеть и сообщества»: обзор до 100 кластеров, таблицы всех подходящих кластеров и компонент.
- «Скачать результаты и проверить данные»: три исходных CSV, распределение ролей и manifest. Фильтры не меняют скачиваемые файлы.
- «О данных и ограничениях» в боковой панели: источник, run_id и кнопка «Обновить результаты».

## Необязательный браузерный QA

Установить Playwright/Chromium отдельно от зависимостей приложения; UI должен быть запущен с тем же outputs:

```powershell
uv pip install --python .venv/Scripts/python.exe playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests/browser_smoke.py --url http://127.0.0.1:3000 --outputs outputs --screenshot demo_outputs/browser-smoke.jpg
.\.venv\Scripts\python.exe tests/browser_graph.py --url http://127.0.0.1:3000 --outputs outputs
```

Последний скрипт сохраняет скриншоты в `/tmp/`; требуется существующий доступный каталог, поэтому на Windows для проверки графа можно использовать Linux/Docker-окружение либо ручной QA. Исторические браузерные результаты не означают новый прогон: см. [QA-документы](README.md).
