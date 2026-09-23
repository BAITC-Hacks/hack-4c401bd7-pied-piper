# Backend: микрозадачи, ожидаемый результат и тестирование

Проверено 23.09.2026 на Windows, Python 3.13.15, исходный HEAD `172c26f`.
Рабочее дерево до проверки было чистым. Добавлены тесты; рабочий код backend не менялся.

## Фактический результат

**168 тестовых случаев прошли в трёх запусках, ошибок и пропусков нет:**

| Запуск | Результат | Время pytest | Артефакт |
| --- | --- | --- | --- |
| Исходный полный набор | 111 passed | 59,84 с | `outputs/backend-scenarios-baseline.xml` |
| Дополнительная матрица API | 56 passed | 47,89 с | `outputs/backend-scenarios-new.xml` |
| Отдельные процессы API/worker, HTTP и перезапуск | 1 passed | 22,13 с | `outputs/backend-http.xml` |

Из 168 случаев: 85 проверяют API/worker, 77 — расчёты, входные данные и протокол
результатов, 6 — существующий UI. Это сумма трёх запусков, а не результат одного
общего запуска после добавления файлов. Дополнительно прошёл независимый CLI-валидатор.
Один тип предупреждения в первых двух запусках: Starlette сообщает об устаревании
адаптера httpx для TestClient. HTTP-тест этого предупреждения не выдал.

## Матрица сценариев

В колонке «Тестирование» приведены имена тестов или файлы с параметризованными проверками.
Везде ниже указан фактически полученный результат.

| № | Микрозадача | Ожидаемый результат | Тестирование и факт |
| --- | --- | --- | --- |
| 1 | Проверить старт и пустое хранилище | Health 200; без bootstrap список datasets пуст; неизвестные ресурсы дают 404 | `test_empty_registry_and_unknown_resources` — пройдено |
| 2 | Проверить повторный bootstrap | Повторный старт не дублирует datasets/runs | `test_bootstrap_schema_and_summary`, HTTP-перезапуск — пройдено |
| 3 | Пройти все маршруты чтения | Все 17 GET-операций отвечают; текущая схема содержит 20 операций на 17 URL-шаблонах | `test_every_documented_operation_has_a_scenario` — пройдено; POST/PUT проверяются отдельными сценариями |
| 4 | Импортировать официальный набор | POST 202, затем worker переводит dataset в ready, counts соответствуют входам | `test_official_import_and_real_recalculation`, HTTP-тест — пройдено |
| 5 | Отклонить повреждённый или неподходящий импорт | Повреждённый Parquet и synthetic-набор не проходят официальный профиль; ошибки доступны в validation | `test_corrupted_import_and_duplicate_fields`, `test_import_failure_and_upload_boundaries` — пройдено |
| 6 | Проверить multipart и размер запроса | Неполный/повторный multipart → 422, JSON вместо multipart → 415, превышение настроенных лимитов → 413 | Существующие тесты импорта — пройдено; лимиты проверены с уменьшенной конфигурацией |
| 7 | Проверить имя и профиль импорта | Пустое имя, пробелы, 121 символ, неизвестный профиль → 422; реестр и каталоги не изменяются | `test_invalid_upload_metadata_leaves_no_dataset`, 4 случая — пройдено |
| 8 | Имитировать ошибку записи импорта | 500 с безопасным сообщением; частичный каталог удалён | `test_upload_failure_cleans_partial_directory` — пройдено |
| 9 | Проверить POST запуска | Требуются корректные dataset_id и Idempotency-Key; неверное тело/JSON → 422, неизвестный dataset → 404 | `test_create_run_http_validation_and_conflicts` — пройдено |
| 10 | Проверить повторные и конкурентные запросы | 4 одновременных HTTP-запроса с одним ключом получают один run и одинаковый Location; другой ключ → ACTIVE_RUN_EXISTS; повтор ключа с другим dataset → IDEMPOTENCY_CONFLICT | `test_create_run_http_validation_and_conflicts`, `test_idempotency_and_concurrent_claims` — пройдено |
| 11 | Проверить успешный worker | queued → succeeded; run_id сохраняется; повтор запроса возвращает завершённый run | `test_worker_calculates_same_id_and_preserves_selection` — пройдено |
| 12 | Запретить чтение незавершённых результатов | Для 14 путей результатов queued → 409 RUN_NOT_READY, неизвестный run → 404 | `test_all_results_reject_queued_and_unknown_runs`, 14 случаев — пройдено |
| 13 | Имитировать сбой расчёта | failed, результат не публикуется, детали исключения не попадают в API | `test_worker_failure_has_no_publication` — пройдено |
| 14 | Изменить входной snapshot после регистрации | Worker отклоняет изменённые входы; старый результат остаётся доступным | `test_changed_input_fails_run_without_replacing_previous_result` — пройдено |
| 15 | Проверить восстановление и владение заданием | Просроченный run/dataset → failed; живой heartbeat сохраняется; старый владелец не публикует; готовые файлы после сбоя БД восстанавливаются в succeeded | 4 recovery-теста и concurrent claims в `test_backend.py` — пройдено |
| 16 | Проверить IDs и ошибки запросов | int64 IDs остаются строками; неканонические IDs, неверные UUID, неизвестные/повторные query-параметры отклоняются | `test_invalid_ids_and_error_shapes`, `test_query_contract` — пройдено |
| 17 | Проверить числовые границы и enum | Отрицательные offsets, дробный limit, NaN/Inf, priority вне [0,1], неверные направления/сортировки/hops/лимиты → 422 | `test_invalid_result_parameters`, 23 случая — пройдено |
| 18 | Проверить фильтрацию, сортировку и страницы | Роль, кластер, компонента, seed, boundary, сочетание фильтров, повтор role и все сортировки совпадают с экспортированными данными; offset даёт нужный срез | `test_filters_and_sorts_match_exported_nodes`, `test_filters_paging_and_top` — пройдено |
| 19 | Проверить карточки и аналитические ограничения | Изолят сохранён; seed inflow отмечен как неполный; boundary отмечен; при нулевом inflow pass_through=null; вклады складываются в score | `test_node_details_ids_and_warnings`, `test_a_verification.py`, `test_role_semantics.py` — пройдено |
| 20 | Проверить направления и веса связей | Для каждого из 24 synthetic-узлов in/out/both совпадают с исходными связями; суммы KZT и n_tx считаются раздельно | `test_edges_and_cluster_graph_preserve_direction_amount_and_count` — пройдено, 72 HTTP-запроса связей |
| 21 | Проверить граф и усечение | Центр и изолят остаются; концы рёбер входят в показанные узлы; hidden=total−shown; полный список связей не обрезается лимитом графа | `test_graphs_edges_and_clusters`, `test_directed_ego_includes_isolate_and_hops` — пройдено |
| 22 | Проверить кластеры | Карточки доступны; размеры покрывают узлы; межкластерные потоки сохраняют направление, сумму и число транзакций | Проверка всех маршрутов и `test_edges_and_cluster_graph_preserve_direction_amount_and_count` — пройдено |
| 23 | Проверить selection | Порядок сохраняется, повторы удаляются, пустой список допустим, >500 уникальных IDs отклоняются; разные run изолированы | `test_selection_is_persistent_atomic_and_exports`, `test_selection_limit_and_run_isolation` — пройдено |
| 24 | Проверить атомарность ошибок selection | Отсутствующее поле, null, строка вместо списка, число/null/неверный gid, лишнее поле → 422; предыдущая запись, включая updated_at, не меняется | `test_invalid_selection_never_overwrites_existing_value`, 7 случаев — пройдено |
| 25 | Проверить CSV | Корректные content-type/имя файла; nodes_roles покрывает все узлы без дублей; top содержит ≥20; исходные CSV отдаются без изменения; selection экспортируется в нужном порядке | `test_export_headers_schema_and_full_coverage`, существующий export-тест — пройдено |
| 26 | Повредить bundle | 503 RESULT_INVALID; старый run не подставляется; ошибки схемы/хешей/метрик выявляются | `test_bad_bundle_is_not_replaced_with_old_result`, `test_validation.py`, `test_bundle_protocol.py` — пройдено |
| 27 | Проверить воспроизводимость и публикацию | Одинаковые входы дают одинаковые аналитические файлы в одной среде; ошибочный candidate не заменяет результат; предел 300 с строгий | `test_stage_callback_and_reproducible_results`, `test_a_verification.py`, `test_bundle_protocol.py` — пройдено |
| 28 | Проверить безопасные ответы ошибок | Тело соответствует ApiError, request_id совпадает с заголовком; внутренние сообщения исключений скрыты | `assert_problem` в новых сценариях, `test_internal_error_is_safe_and_traceable` — пройдено |
| 29 | Выполнить реальный HTTP-цикл | Импорт → worker → расчёт → top → карточки/графы → selection → четыре CSV | `test_official_http_workflow_and_api_worker_restart` — пройдено |
| 30 | Перезапустить API и worker | Selection, dataset/run и хеши трёх полных CSV не изменяются | Тот же HTTP-тест: оба процесса остановлены и запущены с прежним отдельным state — пройдено |
| 31 | Проверить официальный bundle независимым CLI | Все входные данные, роли и результаты согласованы | `backend/validate.py --data data --out outputs` — passed, warnings=[] |

## Факты сквозного HTTP-теста

- Расчёт: `b392fe5b-a3cf-4cea-9a31-d6b8dfd85a74`.
- Время расчёта с очередью: 3,987 с; это измерение на этой машине, не нагрузочный норматив.
- 2 248 узлов, 3 119 направленных пар, 4 840 транзакций, 81 seed, 35 компонент, 105 кластеров.
- После перезапуска сохранились 3 выбранных клиента и байтовые хеши трёх CSV.
- Тест использует временный state и свободный локальный порт; созданные им процессы
  завершаются в finally. Пользовательский runtime не используется.
- CLI-валидатор отдельно подтвердил 19 изолятов и Top-20 сохранённого bundle.

## Повторение

Из корня репозитория в PowerShell:

```powershell
$env:PYTHONUTF8 = '1'
# API, worker, матрица сценариев и реальный HTTP:
.\.venv\Scripts\python.exe -m pytest tests/test_backend.py tests/test_backend_scenarios.py tests/test_backend_http.py -q
# Весь набор, включая аналитические и существующие UI-тесты:
.\.venv\Scripts\python.exe -m pytest -q
# Независимая проверка сохранённого результата:
.\.venv\Scripts\python.exe -m backend.validate --data data --out outputs
```

Для официальных сценариев нужны три исходных Parquet в `data/` и корректный
исходный bundle в `outputs/`. JUnit XML текущей проверки лежат в игнорируемом
`outputs/`; в Git не добавляются.

## Границы покрытия

Проверены все текущие API-операции и перечисленные бизнес-сценарии. Это не доказательство
отсутствия ошибок при любом сочетании входов. В этом запуске не выполнялись Docker/Linux,
нагрузочное/длительное тестирование, исчерпание диска и памяти, физический сбой SQLite,
проверка всех вариантов обрыва сети и multipart, browser E2E и аудит безопасности.
Процент покрытия строк/ветвей не измерялся. Тест лимитов загрузки использует уменьшенные
лимиты; загрузка реальных 50/151 MiB и chunked-передача отдельно не проверялись.
Ранее записанные результаты в `backend-verification.md` не считаются результатами этого запуска.
