# Pied Piper: аналитика графа переводов

Локальный pipeline проверяет три исходных Parquet, рассчитывает признаки, роли и
приоритеты для AML-обзора, затем публикует результат только после проверки
независимым `validate.py`. Роли — сигналы для ручной проверки, а не вывод о
виновности клиента.

Из корня проекта в PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe pipeline.py --data data --out outputs
.\.venv\Scripts\python.exe validate.py --data data --out outputs
.\.venv\Scripts\python.exe -m pytest -q
```

Для запуска нужны `data/nodes.parquet`, `data/edges.parquet` и
`data/transactions.parquet`. Их нет в checkout. Официальный набор описан в
[data/README.md](data/README.md); pipeline проверяет 2248 узлов, а независимый
валидатор также сверяет 3119 пар, 4840 транзакций и 81 seed.
В данном рабочем пространстве исходные файлы доступны без копирования:

```powershell
.\.venv\Scripts\python.exe pipeline.py --data ..\Финансы\data\data --out outputs
.\.venv\Scripts\python.exe validate.py --data ..\Финансы\data\data --out outputs
```

Успешный запуск создаёт неизменяемый каталог `outputs/runs/<UUID>/` с тремя
CSV, `node_metrics.parquet`, `edges.parquet` и `run.json`. Указатель
`outputs/current.json` меняется после успешной candidate-проверки. Для
малого синтетического набора можно передать одинаковый
`--expected-nodes N` в pipeline и validator; такой запуск помечается
`data_kind=synthetic` и не считается приёмкой официальных данных.

Реальная проверка в этом workspace: независимый validator вернул `passed`;
получено 105 кластеров, 35 компонент и 20 строк Top. Два запуска дали
побайтно одинаковые CSV и одинаковые значения `node_metrics.parquet`;
длительность от чтения до candidate-проверки составила 2,38 и 2,27 с.
Streamlit AppTest открыл опубликованный набор без исключений. Разметки
истинных ролей нет, поэтому качество классификации по accuracy/F1 не оценивалось.
