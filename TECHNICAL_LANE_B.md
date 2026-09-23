# Lane B — UI и validator контракта v1.0.0

Источник схем — `src/contracts.py`, владелец A. Reader, UI и независимый validator B поддерживают этот контракт: опубликованные запуски, `current.json`, staging/candidate, все обязательные метрики и nullable-поля. Проверены синтетические данные; готовая реальная выгрузка A ещё нужна для приёмки всего проекта.

## Установка и запуск

Из корня, Python 3.12 (проверенная версия):

```bash
uv venv .venv
uv pip sync --python .venv/bin/python requirements.lock
./run-ui.sh --outputs outputs
```

Без uv: создать venv средствами Python, установить `requirements.lock` через pip. После установки основная работа полностью локальная; внешние API/CDN не нужны, телеметрия Streamlit отключена.

Обычная проверка **опубликованного** расчёта:

```bash
.venv/bin/python validate.py --data data --out outputs
```

В outputs ожидается:

```text
outputs/
  current.json
  runs/<UUID>/
    run.json
    nodes_roles.csv
    clusters.csv
    top_nodes.csv
    node_metrics.parquet
    edges.parquet
```

`current.json` содержит `schema_version: "1.0.0"` и `run_id: "<UUID>"`. Читатель разрешает его один раз, проверяет UUID и совпадение run_id с manifest. Прямой путь к `runs/<UUID>` также поддерживается. Старый формат с числовой версией 1 отклоняется. Повреждённый текущий запуск не подменяется предыдущим.

`run.json` проверяется по `RunManifest`: status=complete, validation.status=passed, validator_version, counts, hashes, versions, seed, thresholds, normalization_scales, priority_weights, stage_runtimes_seconds, role_distribution и warnings. Финальное заявленное total должно быть <300 секунд; сам замер полного pipeline делает A. UI читает только опубликованный bundle, без обращения к исходному data/.

## Вызов из pipeline A до публикации

1. Записать пять output-файлов в `outputs/.staging/<UUID>/` по `OUTPUT_SCHEMAS`.
2. Записать `candidate.json`: все поля RunManifest, кроме validation; status=candidate. Все ключи stage_runtimes_seconds присутствуют; validation/total могут быть предварительными неотрицательными значениями.
3. Выполнить:

```bash
.venv/bin/python validate.py --data data --out outputs/.staging/<UUID> --candidate
```

При успехе stdout содержит **один JSON**:

```json
{"status": "passed", "validator_version": "1.0.0"}
```

Отчёт/предупреждения выводятся в stderr. Ошибка даёт exit=1, сообщение в stderr и не возвращает успешный JSON. Validator ничего не пишет: не публикует candidate, не меняет current.json и не создаёт run.json. UI не открывает candidate.

4. A фиксирует окончательные длительности и validation, удаляет candidate.json, пишет run.json последним, переносит каталог в runs/<UUID> и атомарно заменяет current.json. Уже опубликованные файлы неизменяемы.

## Что проверяет validator

- Имена, порядок и типы обязательных полей по contracts.py; дополнительные колонки допускаются после обязательных.
- Signed int64 без промежуточного float. Для UI gid становится строкой; сравнение/сортировка — численно.
- Покрытие nodes, 2248 строк по умолчанию, словарь ролей, оценки [0,1], evidence с числами длиной до 200, Top ≥20 и согласованность таблиц.
- Кластерные размеры, seed, внутренние суммы, top_gids (1..5), отсутствие смешения компонент; каноническая нумерация компонент и сообществ.
- Степени, суммы, n_tx, флаги boundary/seed, pass_through и nullable при нулевом входе, расстояние до seed, межкластерные связи и normalization scales.
- Допуски ролей, нулевые score при недопуске, причины, выбор основной/альтернативной роли, margin и формулу role_score, предел уверенности boundary/изолятов.
- Четыре компоненты priority, веса, каждый contribution и их сумму с SCORE_ATOL=1e-12.
- Хеши пяти output и трёх input-файлов; совпадение профилей/связей bundle с входом; даты июля 2026, порог 5000, сверку агрегатов transactions с edges без удаления повторов.
- Counts/role_distribution manifest, NULL/NaN/Infinity, неподдерживаемые версии, статусы и небезопасный run_id.

Это проверка структурной и арифметической согласованности, **не доказательство качества AML-классификации**. Validator не пересчитывает PageRank/betweenness, не обучает/не вызывает engine и не доказывает содержательную корректность каждого eligibility gate. Семантические тесты правил, фактическое время pipeline и объяснимость произвольных gid остаются задачами A + Product QA.

## Синтетический пример

```bash
.venv/bin/python tests/fixtures/ui/make_fixture.py --out demo_outputs --data demo_data
.venv/bin/python validate.py --data demo_data --out demo_outputs --expected-nodes 24
./run-ui.sh --outputs demo_outputs
```

Fixture содержит 24 искусственных gid выше `2^53`, цепочку, boundary и изолированный seed, все поля контракта, три исходные тестовые таблицы и опубликованный run. Роли служат проверке UI/протокола, не являются аналитическим результатом. `data_kind: "synthetic"` вызывает постоянную заметную пометку в интерфейсе. Малый expected-nodes разрешён только для synthetic, официальный набор не может выдаваться за synthetic и наоборот. Исходный data/ генератор по умолчанию не меняет.

## Интерфейс и QA

Есть обзор кластеров/компонент, поиск gid, фильтры role/cluster/component/priority, направленный ego 1–2 шага, карточка, рассчитанные вклады приоритета, полные таблицы входящих/исходящих связей и скачивание исходных CSV. Поиск проверяет весь набор независимо от фильтров и открывает разбор клиента. У boundary/seed — явные оговорки. Все nullable-признаки доступны в раскрываемой таблице.

До 100 сообществ/100 узлов ego и 350 показанных рёбер; сокращение обозначено, прямые связи в таблицах не ограничены. Обновление current.json подхватывается при следующем взаимодействии/кнопке обновления; одна страница использует один неизменяемый запуск.

```bash
.venv/bin/python -m pytest -q
```

Проверено: 46 тестов прошли; Chromium smoke с опубликованным v1.0.0 прошёл (boundary/изолят/неизвестный gid), без page errors.

Тесты покрывают CSV, SHA256, арифметику метрик, точность int64, nullable-типы, переключение публикаций, traversal в run_id, candidate без run.json, отсутствие записей при валидации, испорченный input, CLI и Streamlit AppTest. Для необязательной проверки Chromium установить Playwright/Chromium отдельно от зависимостей приложения и запустить `python tests/browser_smoke.py` на UI с synthetic fixture.

**Открытый пункт:** получить реальный завершённый запуск A, прогнать validator на 2248 клиентах и браузерный QA на произвольных реальных gid. Synthetic pass не закрывает этот пункт.
