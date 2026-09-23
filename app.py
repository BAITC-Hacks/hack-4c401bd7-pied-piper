"""Money Graph, Technical Lane B: read-only analyst workspace."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.view import FILES, LABELS, load_bundle, make_graph, select_ego
from src.graph_ui import ego_html, overview_html
from validate import ValidationError
from src.bundle_io import resolve_run

st.set_page_config(page_title='Money Graph · Аналитика потоков', page_icon='◈', layout='wide')


@st.cache_data(show_spinner=False)
def cached_bundle(path, fingerprint):
    return load_bundle(Path(path))


@st.cache_data(show_spinner=False)
def cached_ego(nodes, edges, gid, hops, color_by, limit):
    graph = make_graph(nodes, edges)
    ego, hidden = select_ego(graph, gid, nodes, hops, limit)
    return ego_html(ego, nodes, gid, color_by), hidden, max(0, ego.number_of_edges()-350)


@st.cache_data(show_spinner=False)
def cached_overview(nodes, edges, clusters, visible):
    from src.view import Bundle
    return overview_html(Bundle(nodes, edges, clusters, pd.DataFrame(), {}, {}, {}), visible)


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--outputs', default='outputs')
    args, _ = parser.parse_known_args()
    directory = Path(args.outputs)
    st.markdown('### MONEY GRAPH')
    st.title('Кого проверить первым — и почему')
    st.caption('Наблюдаемые денежные потоки · июль 2026 · рабочее место AML-аналитика')
    with st.sidebar:
        st.header('Навигация')
        if st.button('Обновить результаты', use_container_width=True):
            st.cache_data.clear()
        st.caption(f'Источник: {directory}')
    try:
        resolved, pointer_id = resolve_run(directory)
        fingerprint = tuple((name, (resolved/name).stat().st_mtime_ns, (resolved/name).stat().st_size)
                            for name in (*FILES, 'run.json'))
        with st.spinner('Проверяем целостность результатов…'):
            bundle = cached_bundle(str(resolved.resolve()), fingerprint)
            if pointer_id is not None and bundle.manifest['run_id'] != pointer_id:
                raise ValidationError('current.json: run_id не совпадает с manifest')
    except (OSError, ValidationError) as exc:
        st.info('Нет завершённого расчёта для просмотра.')
        st.error(str(exc))
        st.markdown('Передайте публикацию Lane A: `current.json` и каталог `runs/<run_id>/` с CSV, метриками, связями и `run.json` версии 1.0.0.')
        st.code('python pipeline.py --data data --out outputs', language='bash')
        st.caption('Команда pipeline относится к Lane A. Интерфейс не рассчитывает и не подменяет роли.')
        st.markdown('Для проверки интерфейса на отдельном синтетическом примере:')
        st.code('python tests/fixtures/ui/make_fixture.py --out demo_outputs\nstreamlit run app.py -- --outputs demo_outputs', language='bash')
        return
    nodes = bundle.nodes
    if bundle.manifest.get('data_kind') == 'synthetic':
        st.warning('СИНТЕТИЧЕСКИЙ ПРИМЕР · Только проверка интерфейса. Это не анализ предоставленного датасета.')
    st.caption('Графовые роли — гипотезы для ручной проверки, не доказательство виновности. '
               'Видны только исходящие ветви обхода и переводы от 5 000 KZT; полные балансы неизвестны.')
    cards = st.columns(5)
    for col, label, value in zip(cards, ['Клиенты', 'Связи', 'Кластеры', 'Компоненты', 'Граница depth=4'],
                                  [len(nodes), len(bundle.edges), len(bundle.clusters), bundle.report['n_components'], int(nodes.depth.eq(4).sum())]):
        col.metric(label, f'{value:,}'.replace(',', ' '))
    with st.sidebar:
        search = st.text_input('Поиск по gid', placeholder='Введите точный gid', key='gid_search').strip()
        selected_roles = st.multiselect('Роль', list(LABELS), format_func=lambda role: f'{LABELS[role]} · {role}')
        cluster = st.selectbox('Кластер', ['Все'] + sorted(bundle.clusters.cluster_id.tolist()))
        component = st.selectbox('Компонента', ['Все'] + sorted(nodes.component_id.unique().tolist()))
        threshold = st.slider('Минимальный приоритет', 0.0, 1.0, 0.0, .01)
        hops = st.radio('Окрестность', [1, 2], format_func=lambda value: f'{value} шаг', horizontal=True)
        node_limit = st.select_slider('Узлов на графе', options=[20, 40, 60, 100], value=40)
        color = st.radio('Цвет узлов', ['role', 'cluster_id'], format_func=lambda value: 'Роль' if value == 'role' else 'Кластер', horizontal=True)
    filtered = nodes[nodes.priority_score.ge(threshold)]
    if selected_roles:
        filtered = filtered[filtered.role.isin(selected_roles)]
    if cluster != 'Все':
        filtered = filtered[filtered.cluster_id.eq(cluster)]
    if component != 'Все':
        filtered = filtered[filtered.component_id.eq(component)]
    filtered = filtered.assign(sort_gid=filtered.gid.map(int)).sort_values(['priority_score', 'sort_gid'], ascending=[False, True]).drop(columns='sort_gid')
    st.caption(f'Под фильтрами: {len(filtered)} из {len(nodes)} клиентов. Точный поиск проверяет весь набор.')
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
        options = filtered.gid.tolist()
        role_lookup = nodes.set_index('gid').role.to_dict()
        gid = st.selectbox('Клиент для разбора', options,
                           format_func=lambda value: f'{value} · {LABELS[role_lookup[value]]}')
    else:
        st.info('Под выбранные фильтры не попал ни один клиент. Измените фильтры или найдите точный gid.')
    # Streamlit 1.49 tabs have no controlled active key. Put the requested client first
    # after a search so a rerun cannot hide its card behind the overview.
    names = (['Разбор клиента', 'Обзор сети'] if search else ['Обзор сети', 'Разбор клиента'])
    names += ['Приоритет проверки', 'Кластеры', 'Качество данных']
    tabs = dict(zip(names, st.tabs(names)))
    overview, investigation, ranking, communities, diagnostics = (
        tabs[name] for name in ['Обзор сети', 'Разбор клиента', 'Приоритет проверки', 'Кластеры', 'Качество данных'])
    with overview:
        st.markdown('Каждая точка — сообщество клиентов. Стрелки показывают наблюдаемые потоки между сообществами.')
        fig, hidden, hidden_edges = cached_overview(nodes, bundle.edges, bundle.clusters, tuple(sorted(filtered.cluster_id.unique())))
        components.html(fig, height=680, scrolling=True)
        if hidden or hidden_edges:
            st.info(f'Для читаемости скрыто {hidden} кластеров и {hidden_edges} межкластерных связей; все кластеры доступны в таблице.')
        st.caption('Изолированные сообщества сохранены. Внутренние потоки отражены в таблице кластеров. '
                   'Фильтры выбирают сообщества, содержащие подходящих клиентов; размеры сообществ остаются полными.')
        summary = nodes.groupby('component_id').agg(n_nodes=('gid', 'size'), n_seed=('is_seed', 'sum'), n_clusters=('cluster_id', 'nunique')).reset_index()
        st.dataframe(summary, hide_index=True, use_container_width=True)
    with investigation:
        if gid is None:
            st.info('Выберите клиента или введите gid в поиске.')
        else:
            graph_column, details = st.columns([1.8, 1])
            row = nodes.set_index('gid').loc[gid]
            with graph_column:
                fig, hidden, hidden_edges = cached_ego(nodes, bundle.edges, gid, hops, color, node_limit)
                components.html(fig, height=680, scrolling=True)
                st.caption('Стрелка: плательщик → получатель. Окрестность включает входящие и исходящие связи; фильтры не скрывают соседей.')
                if hidden or hidden_edges:
                    st.info(f'Показаны до {node_limit} узлов и 350 рёбер. Скрыто узлов: {hidden}; рёбер среди показанных узлов: {hidden_edges}. Все прямые связи — ниже.')
            with details:
                st.subheader(f'Клиент {gid}')
                st.markdown(f'**{LABELS[row.role]}** · `{row.role}`')
                c1, c2 = st.columns(2)
                c1.metric('Уверенность в роли', f'{row.role_score:.3f}')
                c2.metric('Приоритет проверки', f'{row.priority_score:.3f}')
                st.caption('Уверенность — поддержка правилами, не вероятность виновности.')
                st.write(f'Кластер {row.cluster_id} · Компонента {row.component_id} · depth={row.depth} · seed={"да" if row.is_seed else "нет"}')
                st.info(row.evidence)
                if row.depth == 4:
                    st.warning('Граница обхода depth=4: дальнейшие переводы неизвестны. Отсутствие выхода не доказывает конечного получателя.')
                if row.is_seed:
                    st.warning('Seed: входящие потоки неполны. Отношение отправлено/получено не является основанием для аномалии.')
                st.metric('Входящий поток', f'{row.in_kzt:,.0f} KZT')
                st.metric('Исходящий поток', f'{row.out_kzt:,.0f} KZT')
                st.write(f'Контрагенты: {row.in_deg} входящих / {row.out_deg} исходящих')
                st.write(f'Транзакции: {row.in_tx} входящих / {row.out_tx} исходящих')
                extra = [name for name in nodes if name.startswith('contribution_')]
                if extra:
                    st.markdown('**Вклады в приоритет из расчёта**')
                    st.dataframe(pd.DataFrame({'Компонента': extra, 'Значение': [row[name] for name in extra]}), hide_index=True)
                top_reason = bundle.top[bundle.top.gid.eq(gid)]
                if not top_reason.empty:
                    st.write(top_reason.iloc[0].why)
                with st.expander('Все рассчитанные признаки'):
                    st.dataframe(pd.DataFrame({'Признак': row.index, 'Значение': [str(v) for v in row]}), hide_index=True)
            inbound, outbound = st.columns(2)
            for col, endpoint, title, other in ((inbound, 'dst', 'Входящие связи', 'src'), (outbound, 'src', 'Исходящие связи', 'dst')):
                links = bundle.edges[bundle.edges[endpoint].eq(gid)].copy()
                links['counterparty_role'] = links[other].map(nodes.set_index('gid').role)
                links['counterparty_cluster'] = links[other].map(nodes.set_index('gid').cluster_id)
                with col:
                    st.markdown(f'**{title} · {len(links)}**')
                    st.dataframe(links.sort_values('sum_kzt', ascending=False), hide_index=True, use_container_width=True)
    with ranking:
        st.subheader('Кандидаты для дальнейшей проверки')
        if filtered.empty:
            st.info('Нет клиентов под выбранными фильтрами.')
        else:
            display = filtered[['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence']].head(100).copy()
            display['why'] = display.gid.map(bundle.top.set_index('gid').why).fillna(display.evidence)
            st.dataframe(display, hide_index=True, use_container_width=True,
                         column_config={'priority_score': st.column_config.ProgressColumn('Приоритет', min_value=0, max_value=1, format='%.3f')})
            st.caption('До 100 первых клиентов под фильтрами. Скачивание ниже содержит исходный Top текущего расчёта.')
        st.download_button('Скачать top_nodes.csv', bundle.raw['top_nodes.csv'], 'top_nodes.csv', 'text/csv')
        st.download_button('Скачать все роли', bundle.raw['nodes_roles.csv'], 'nodes_roles.csv', 'text/csv')
    with communities:
        visible = bundle.clusters[bundle.clusters.cluster_id.isin(filtered.cluster_id)]
        st.dataframe(visible, hide_index=True, use_container_width=True)
        st.download_button('Скачать clusters.csv', bundle.raw['clusters.csv'], 'clusters.csv', 'text/csv')
    with diagnostics:
        st.success('SHA256 всех пяти файлов проверены. CSV и метрики согласованы со связями.')
        for warning in bundle.report['warnings']:
            st.warning(warning)
        distribution = nodes.groupby(['role', 'depth', 'is_seed']).size().rename('n_nodes').reset_index()
        st.dataframe(distribution, hide_index=True, use_container_width=True)
        st.caption(f'Изолированных клиентов: {bundle.report["n_isolates"]}. '
                   'Повторные строки транзакций нельзя удалять без transaction ID. '
                   'Даты имеют точность день; ground truth ролей отсутствует.')
        st.json(bundle.manifest, expanded=False)


main()
