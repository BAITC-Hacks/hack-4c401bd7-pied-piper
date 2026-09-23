"""Read-only analyst workspace: choose a client, understand why, inspect flows."""
from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from frontend.view import FILES, LABELS, load_bundle, make_graph, select_ego
from frontend.graph_ui import ego_html, overview_html
from frontend.presentation import ROLE_GUIDANCE, priority_parts, priority_explanation
from backend.validate import ValidationError
from backend.core.bundle_io import resolve_run
from backend.core.csv_export import csv_download_bytes
from backend.core.explanations import role_gate
from backend.core.resilience import compare_removals

st.set_page_config(page_title='Money Graph · Очередь проверки', page_icon='◈', layout='wide')
# Keep Streamlit tools (including Clear cache); hide only the deployment shortcut.
st.html('<style>[data-testid="stAppDeployButton"] { display: none; }</style>')
# Cached HTML must change when its renderer/template changes, even for the same run.
GRAPH_REVISION = sha256(b''.join((Path(__file__).parent / name).read_bytes()
                                for name in ('graph_ui.py', 'graph.html'))).hexdigest()


@st.cache_data(show_spinner=False)
def cached_bundle(path, fingerprint):
    return load_bundle(Path(path))


@st.cache_data(show_spinner=False)
def cached_ego(nodes, edges, gid, hops, color_by, limit, graph_revision):
    graph = make_graph(nodes, edges)
    ego, hidden = select_ego(graph, gid, nodes, hops, limit)
    return ego_html(ego, nodes, gid, color_by), hidden, max(0, ego.number_of_edges()-350)


@st.cache_data(show_spinner=False)
def cached_overview(nodes, edges, clusters, visible, graph_revision):
    from frontend.view import Bundle
    return overview_html(Bundle(nodes, edges, clusters, pd.DataFrame(), {}, {}, {}), visible)


@st.cache_data(show_spinner=False)
def cached_resilience(nodes, edges, n):
    return compare_removals(nodes, edges, n)


def render_resilience(bundle):
    st.subheader('Что будет без ключевых узлов?')
    st.write('Сравните исключение клиентов по нашему приоритету и просто по обороту. '
             'Эксперимент использует всю сеть, независимо от фильтров и лимитов графа.')
    n = st.select_slider('Сколько клиентов исключить из модели', options=[1, 3, 5, 10],
                         value=5, key='resilience_n')
    result = cached_resilience(bundle.nodes, bundle.edges, n)
    labels = {'baseline': 'Исходная сеть', 'priority': 'По приоритету', 'turnover': 'По обороту'}
    fields = [
        ('Исключено клиентов', 'removed_nodes', 'count'),
        ('Осталось клиентов', 'remaining_nodes', 'count'),
        ('Связных групп', 'components', 'count'),
        ('Крупнейшая группа, клиентов', 'largest_component', 'count'),
        ('Крупнейшая / оставшиеся', 'largest_share', 'percent'),
        ('Новых изолятов', 'new_isolates', 'count'),
        ('Разобщённых пар оставшихся', 'disconnected_pair_share', 'percent'),
        ('Затронуто переводов, KZT', 'affected_kzt', 'money'),
        ('Доля суммы переводов', 'affected_share', 'percent'),
    ]
    rows = []
    for title, key, kind in fields:
        row = {'Показатель': title}
        for strategy, label in labels.items():
            value = result[strategy][key]
            row[label] = (f'{value * 100:.2f}%' if kind == 'percent' else
                          f'{value:,.2f}'.replace(',', ' ') if kind == 'money' else str(value))
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', height=355)
    st.caption('Связные группы — слабые компоненты, включая изоляты: направление не учитывается '
               'только при проверке связности. Разобщённые пары — оставшиеся клиенты, ранее '
               'связанные путём, а теперь разделённые; исключённые клиенты в знаменатель не входят. '
               'Перевод между двумя исключёнными клиентами учитывается в сумме один раз.')
    st.caption('Приоритет ручной проверки не оптимизировался для разрушения связности. '
               'Более сильный распад в одном сценарии не доказывает более точное выявление нарушений.')
    with st.expander('Какие клиенты исключены и как выбран порядок'):
        for strategy in ('priority', 'turnover'):
            st.markdown(f'**{labels[strategy]}**')
            st.code(', '.join(result[strategy]['removed_gids']) or 'Нет клиентов', language=None)
        st.caption('Оборот = наблюдаемый вход + выход. При равенстве — gid численно по возрастанию. '
                   'Оба списка фиксируются до исключения; роли и приоритеты не пересчитываются.')
    st.warning('Это симуляция на неполной наблюдаемой сети, не рекомендация блокировки счетов. '
               'Затронутый объём — исторические переводы, не предотвращённый ущерб. '
               'Перенаправление потоков не моделируется. Роли, CSV и исходные файлы не меняются.')


def choose_client(gid):
    st.session_state['active_gid'] = gid
    st.session_state['gid_search'] = ''
    st.session_state['workspace'] = 'Проверка клиентов'


def search_changed():
    st.session_state['workspace'] = 'Проверка клиентов'


def choose_from_list():
    if st.session_state.get('queue_client'):
        choose_client(st.session_state['queue_client'])


def money(value):
    return f'{value:,.2f} KZT'.replace(',', ' ')


def render_client(bundle, row, ranks):
    st.subheader(f'Клиент {row.gid}')
    st.caption(f'Место №{ranks[row.gid]} из {len(bundle.nodes)} в общей очереди · Сообщество {row.cluster_id}')
    st.markdown(f'**{LABELS[row.role]}**')
    st.write(ROLE_GUIDANCE[row.role][0])
    cols = st.columns(2)
    cols[0].metric('Приоритет проверки', f'{row.priority_score * 100:.1f} / 100')
    cols[1].metric('Поддержка роли', f'{row.role_score:.3f}')
    st.caption('Приоритет задаёт порядок ручной проверки. Поддержка роли от 0 до 1 отражает согласованность правил. Оба показателя — не вероятность нарушения.')
    st.markdown('**Почему стоит посмотреть**')
    st.info(row.evidence)
    st.markdown('**Почему назначена эта роль**')
    st.write(role_gate(row, bundle.manifest['thresholds']))
    if row.role != 'peripheral':
        st.caption('Среди допущенных ролей выбрана роль с максимальной базовой оценкой. Пороги рассчитаны по текущей выборке; числа показаны с округлением.')
    if pd.notna(row.alternative_role):
        st.write(f'Ближайшая альтернатива: **{LABELS[row.alternative_role]}**. '
                 f'Базовые оценки: {row[f"score_{row.role}"]:.3f} и {row.alternative_score:.3f}; '
                 f'разрыв {row.role_margin:.3f}. Чем меньше разрыв, тем менее однозначен выбор.')
    st.write(priority_explanation(row))
    with st.expander('Из чего складывается приоритет'):
        st.dataframe(pd.DataFrame(priority_parts(row), columns=['Фактор', 'Баллы из 100']), hide_index=True,
                     column_config={'Баллы из 100': st.column_config.NumberColumn(format='%.2f')})
        st.caption('Сумма четырёх вкладов равна приоритету. Веса: положение в сети 35%, близость к исходным клиентам 20%, масштаб 30%, поддержка роли 15%.')
    cols = st.columns(2)
    cols[0].metric('Входящий поток', money(row.in_kzt))
    cols[1].metric('Исходящий поток', money(row.out_kzt))
    st.write(f'Контрагенты: {row.in_deg} входящих / {row.out_deg} исходящих')
    st.caption(f'Транзакции: {row.in_tx} входящих / {row.out_tx} исходящих. Суммы наблюдаемые, это не баланс счёта.')
    if row.depth == 4:
        st.warning('Граница обхода depth=4: дальнейшие переводы неизвестны. Отсутствие выхода не доказывает конечного получателя.')
    if row.is_seed:
        st.warning('Seed: входящие потоки неполны. Это исходная точка сбора данных; этот статус сам по себе не означает нарушение.')
    if row.in_deg == 0 and row.out_deg == 0:
        st.info('У клиента нет наблюдаемых связей в этом наборе. Он сохранён в расчёте, но отсутствие переводов здесь не доказывает отсутствие активности.')
    st.markdown('**Что проверить дальше**')
    st.write(ROLE_GUIDANCE[row.role][1])
    with st.expander('Альтернативная роль и все признаки'):
        if pd.notna(row.alternative_role):
            st.write(f'Альтернатива: {LABELS[row.alternative_role]}. Разрыв базовых оценок: {row.role_margin:.3f}.')
        else:
            st.write('Других допустимых содержательных ролей нет.')
        st.dataframe(pd.DataFrame({'Признак': row.index, 'Значение': [str(v) for v in row]}), hide_index=True)


def render_links(bundle, gid):
    lookup = bundle.nodes.set_index('gid')
    st.subheader('Переводы: кто отправлял и кто получал')
    st.caption('Все прямые связи выбранного клиента, включая скрытые на графе. Крупнейшие суммы — сверху.')
    for col, endpoint, title, other in zip(st.columns(2), ('dst', 'src'), ('Входящие связи', 'Исходящие связи'), ('src', 'dst')):
        links = bundle.edges[bundle.edges[endpoint].eq(gid)].sort_values('sum_kzt', ascending=False).copy()
        with col:
            st.markdown(f'**{title} · {len(links)}**')
            if links.empty:
                st.caption('В выборке таких переводов нет.')
            display = links[[other, 'sum_kzt', 'n_tx']].rename(columns={other: 'Контрагент', 'sum_kzt': 'Сумма, KZT', 'n_tx': 'Переводов'})
            display['Роль'] = display['Контрагент'].map(lookup.role).map(LABELS)
            st.dataframe(display, hide_index=True, column_config={'Сумма, KZT': st.column_config.NumberColumn(format='%.2f')})


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--outputs', default='outputs')
    args, _ = parser.parse_known_args()
    directory = Path(args.outputs)
    st.caption('MONEY GRAPH · Наблюдаемые переводы за июль 2026')
    with st.sidebar:
        st.header('Money Graph')
        workspace = st.radio('Рабочее место', ['Проверка клиентов', 'Сеть и сообщества'], key='workspace')
        search = st.text_input('Поиск по gid', placeholder='Полный идентификатор клиента', key='gid_search', on_change=search_changed).strip()
        st.caption('Точный поиск работает независимо от фильтров.')
    try:
        resolved, pointer_id = resolve_run(directory)
        fingerprint = tuple((name, (resolved/name).stat().st_mtime_ns, (resolved/name).stat().st_size) for name in (*FILES, 'run.json'))
        bundle = cached_bundle(str(resolved.resolve()), fingerprint)
        if pointer_id is not None and bundle.manifest['run_id'] != pointer_id:
            raise ValidationError('current.json: run_id не совпадает с manifest')
    except (OSError, ValidationError) as exc:
        st.info('Нет завершённого расчёта для просмотра.')
        st.error(str(exc))
        st.code('python -m backend.pipeline --data data --out outputs', language='bash')
        st.caption('После расчёта обновите страницу. Для тестового примера используйте tests/fixtures/ui/make_fixture.py и отдельный demo_outputs.')
        return
    nodes = bundle.nodes
    if bundle.manifest.get('data_kind') == 'synthetic':
        st.warning('СИНТЕТИЧЕСКИЙ ПРИМЕР · Только проверка интерфейса. Это не анализ предоставленного датасета.')
    with st.sidebar:
        with st.expander('Фильтры очереди'):
            selected_roles = st.multiselect('Роль', list(LABELS), format_func=lambda role: LABELS[role])
            cluster = st.selectbox('Сообщество', ['Все'] + sorted(bundle.clusters.cluster_id.tolist()))
            component = st.selectbox('Компонента', ['Все'] + sorted(nodes.component_id.unique().tolist()))
            threshold = st.slider('Минимальный приоритет', 0.0, 1.0, 0.0, .01)
        with st.expander('О данных и ограничениях'):
            st.write(f'{len(nodes):,} клиентов · {len(bundle.edges):,} связей · {len(bundle.clusters)} сообществ'.replace(',', ' '))
            st.write(f'Видны исходящие ветви от {bundle.report["n_seed"]} исходных клиентов, переводы от 5 000 KZT и максимум четыре шага. Полная история счетов неизвестна.')
            st.caption(f'Источник: {directory} · run {bundle.manifest["run_id"]}')
            if st.button('Обновить результаты'):
                st.cache_data.clear()
                st.rerun()
    ordered = nodes.assign(sort_gid=nodes.gid.map(int)).sort_values(['priority_score', 'sort_gid'], ascending=[False, True]).drop(columns='sort_gid')
    ranks = {gid: i + 1 for i, gid in enumerate(ordered.gid)}
    filtered = ordered[ordered.priority_score.ge(threshold)]
    if selected_roles:
        filtered = filtered[filtered.role.isin(selected_roles)]
    if cluster != 'Все':
        filtered = filtered[filtered.cluster_id.eq(cluster)]
    if component != 'Все':
        filtered = filtered[filtered.component_id.eq(component)]
    gid = None
    if search:
        canonical = str(int(search)) if search.lstrip('-').isdigit() else search
        if canonical in set(nodes.gid):
            gid = canonical
            if gid not in set(filtered.gid):
                st.info('Найденный gid вне текущих фильтров. Его карточка и полные связи показаны независимо от фильтров.')
        else:
            st.warning(f'Клиент с gid «{search}» не найден.')
    elif not filtered.empty:
        current = st.session_state.get('active_gid')
        gid = current if current in set(filtered.gid) else filtered.iloc[0].gid
    if filtered.empty:
        st.info('Под выбранные фильтры не попал ни один клиент. Измените фильтры или найдите точный gid.')
    if workspace == 'Проверка клиентов':
        st.title('Кого проверить первым')
        st.caption('Выберите клиента в очереди → прочитайте объяснение → проверьте его переводы. Роли — гипотезы, не доказательство виновности.')
        queue, detail = st.columns([1, 2.2], gap='large')
        with queue:
            st.subheader('Очередь проверки')
            st.caption(f'{len(filtered)} из {len(nodes)} клиентов под фильтрами. Первые 8 — ниже; балл от 0 до 100 задаёт порядок проверки.')
            for row in filtered.head(8).itertuples():
                st.button(f'№{ranks[row.gid]} · {row.gid}  \n{LABELS[row.role]} · {row.priority_score * 100:.1f} / 100',
                          key=f'pick_{row.gid}', on_click=choose_client, args=(row.gid,),
                          type='primary' if row.gid == gid else 'secondary', use_container_width=True)
            with st.expander('Найти другого клиента в очереди'):
                role_lookup = nodes.set_index('gid').role.to_dict()
                st.selectbox('Клиент из очереди', [''] + filtered.gid.tolist(), key='queue_client',
                             format_func=lambda value: 'Выберите клиента' if not value else f'№{ranks[value]} · {value} · {LABELS[role_lookup[value]]}',
                             on_change=choose_from_list)
                st.dataframe(filtered[['gid', 'role', 'priority_score', 'evidence']].head(100), hide_index=True)
        with detail:
            if gid is None:
                st.info('Выберите клиента в очереди или введите gid в поиске слева.')
            else:
                render_client(bundle, nodes.set_index('gid', drop=False).loc[gid], ranks)
        if gid is not None:
            st.divider()
            st.subheader('Как связаны переводы клиента')
            st.caption('Стрелка показывает направление денег: отправитель → получатель. Точки можно двигать. Полные связи — в таблицах ниже.')
            controls = st.columns([1, 1, 1])
            hops = controls[0].radio('Окрестность', [1, 2], format_func=lambda value: 'Прямые связи' if value == 1 else 'Ещё один шаг', horizontal=True)
            node_limit = controls[1].select_slider('Узлов на графе', options=[20, 40, 60, 100], value=40)
            color = controls[2].radio('Цвет узлов', ['role', 'cluster_id'], format_func=lambda value: 'Роль' if value == 'role' else 'Сообщество', horizontal=True)
            html, hidden, hidden_edges = cached_ego(nodes, bundle.edges, gid, hops, color, node_limit, GRAPH_REVISION)
            components.html(html, height=680, scrolling=True)
            if hidden or hidden_edges:
                st.info(f'Показаны до {node_limit} узлов и 350 рёбер. Скрыто узлов: {hidden}; рёбер среди показанных узлов: {hidden_edges}. Все прямые связи — ниже.')
            render_links(bundle, gid)
    else:
        st.title('Как устроена сеть')
        st.write('Одна точка — сообщество клиентов, стрелка — переводы между сообществами. Несвязанные группы расположены вокруг основной сети для удобства просмотра: близость на экране не означает финансовую связь.')
        cols = st.columns(4)
        for col, label, value in zip(cols, ['Клиенты', 'Сообщества', 'Несвязанные части', 'Клиенты без связей'],
                                  [len(nodes), len(bundle.clusters), bundle.report['n_components'], bundle.report['n_isolates']]):
            col.metric(label, value)
        with st.expander('Устойчивость сети: исключение ключевых узлов'):
            render_resilience(bundle)
        html, hidden, hidden_edges = cached_overview(nodes, bundle.edges, bundle.clusters, tuple(sorted(filtered.cluster_id.unique())), GRAPH_REVISION)
        components.html(html, height=680, scrolling=True)
        if hidden or hidden_edges:
            st.info(f'Для читаемости скрыто {hidden} сообществ и {hidden_edges} межкластерных связей; все сообщества доступны в таблице.')
        st.caption('Для объяснения роли отдельного клиента откройте «Проверка клиентов» или введите gid в поиск.')
        with st.expander('Все сообщества и гипотезы', expanded=True):
            st.dataframe(bundle.clusters[bundle.clusters.cluster_id.isin(filtered.cluster_id)], hide_index=True,
                         column_config={'cluster_id': 'Сообщество', 'n_nodes': 'Клиентов', 'n_seed': 'Исходных клиентов', 'sum_kzt_internal': 'Внутренние переводы, KZT', 'top_gids': 'Клиенты с высоким приоритетом', 'hypothesis': 'Наблюдаемый паттерн'})
        with st.expander('Несвязанные части сети'):
            st.dataframe(nodes.groupby('component_id').agg(n_nodes=('gid', 'size'), n_seed=('is_seed', 'sum'), n_clusters=('cluster_id', 'nunique')).reset_index(), hide_index=True)
    st.divider()
    with st.expander('Скачать результаты и проверить данные'):
        st.caption('Выгрузки содержат полный результат текущего расчёта; фильтры экрана их не изменяют.')
        cols = st.columns(3)
        for col, filename, label in zip(cols, ['top_nodes.csv', 'nodes_roles.csv', 'clusters.csv'], ['Скачать top_nodes.csv', 'Скачать все роли', 'Скачать clusters.csv']):
            col.download_button(label, csv_download_bytes(bundle.raw[filename]), filename, 'text/csv; charset=utf-8')
        st.success('SHA256 всех пяти файлов проверены. CSV и метрики согласованы со связями.')
        for warning in bundle.report['warnings']:
            st.warning(warning)
        st.dataframe(nodes.groupby(['role', 'depth', 'is_seed']).size().rename('n_nodes').reset_index(), hide_index=True)
        st.caption('Даты имеют точность день. Независимой разметки ролей нет. Manifest содержит версии, пороги, веса и время расчёта.')
        st.json(bundle.manifest, expanded=False)


main()
