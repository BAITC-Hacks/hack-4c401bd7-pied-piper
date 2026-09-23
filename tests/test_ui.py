import sys
from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1]/'frontend/app.py')


def test_ui_empty_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['app.py', '--outputs', str(tmp_path/'missing')])
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception
    assert any('Нет завершённого' in message.value for message in app.info)


def test_ui_search_filters_isolate_and_boundary(sample, monkeypatch):
    out, _, gids = sample
    monkeypatch.setattr(sys, 'argv', ['app.py', '--outputs', str(out)])
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception
    assert any('СИНТЕТИЧЕСКИЙ' in message.value for message in app.warning)
    app.text_input(key='gid_search').set_value(gids[22]).run()
    assert not app.exception
    assert app.radio(key='workspace').value == 'Проверка клиентов'
    assert any('Что проверить дальше' in item.value for item in app.markdown)
    assert any('Почему назначена эта роль' in item.value for item in app.markdown)
    assert any('Граница обхода depth=4' in message.value for message in app.warning)
    links = [item.value for item in app.dataframe if 'Контрагент' in item.value]
    assert len(links) == 2
    assert links[0]['Контрагент'].tolist() == [gids[3]]
    assert links[1].empty
    app.slider[0].set_value(1.0).run()
    assert any('вне текущих фильтров' in message.value for message in app.info)
    app.text_input(key='gid_search').set_value(gids[-1]).run()
    assert not app.exception
    assert any(gids[-1] in message.value for message in app.subheader)
    app.text_input(key='gid_search').set_value('999').run()
    assert any('не найден' in message.value for message in app.warning)
    app.text_input(key='gid_search').set_value('').run()
    assert any('не попал ни один' in message.value for message in app.info)
    assert not app.exception


def test_queue_click_and_search_from_overview(sample, monkeypatch):
    out, _, gids = sample
    monkeypatch.setattr(sys, 'argv', ['app.py', '--outputs', str(out)])
    app = AppTest.from_file(APP, default_timeout=30).run()
    choices = [button for button in app.button if button.key and button.key.startswith('pick_')]
    chosen = choices[1].key.removeprefix('pick_')
    choices[1].click().run()
    assert not app.exception
    assert any(item.value == f'Клиент {chosen}' for item in app.subheader)
    app.radio(key='workspace').set_value('Сеть и сообщества').run()
    assert not app.exception
    assert any(item.value == 'Как устроена сеть' for item in app.title)
    app.text_input(key='gid_search').set_value(gids[22]).run()
    assert app.radio(key='workspace').value == 'Проверка клиентов'
    assert any(item.value == f'Клиент {gids[22]}' for item in app.subheader)
    assert not app.exception
