"""Explain saved role gates; never calculate or change role assignments."""


def role_gate(row, thresholds, *, compact=False):
    role = row.role
    if role == 'peripheral':
        return 'Правила 5 основных ролей не выполнены.' if compact else (
            'Ни одно правило допуска к пяти основным ролям не выполнено. '
            'Это не означает отсутствие риска.')
    if role == 'consolidator':
        minimum = thresholds['in_degree_min']
        return (f'Порог входа: ≥{minimum} контр.' if compact else
                f'{row.in_deg} отправителей ≥ порога {minimum}: выполнено правило накопления средств.')
    if role == 'distributor':
        minimum = thresholds['out_degree_min']
        return (f'Порог выхода: ≥{minimum} контр.' if compact else
                f'{row.out_deg} получателей ≥ порога {minimum}: выполнено правило распределения средств.')
    if role == 'transit':
        minimum = thresholds['transit_balance_min']
        balance = min(row.pass_through, 1 / row.pass_through)
        return (f'Баланс {balance:.6g} ≥ {minimum:.6g}.' if compact else
                f'Баланс потоков min(выход/вход, вход/выход) = {balance:.6g} '
                f'≥ порога {minimum:.6g}. Есть вход и выход; клиент не seed и не на границе depth=4. '
                'Это сходство объёмов, а не доказательство быстрого перевода тех же денег.')
    if role == 'terminal':
        minimum = thresholds['terminal_in_kzt_min']
        return (f'Порог входа ≥{minimum:.2f} KZT; depth={row.depth}<4.' if compact else
                f'Вход {row.in_kzt:,.2f} KZT ≥ порога {minimum:,.2f} KZT; '
                f'исходящих связей 0; depth={row.depth}<4. '
                'Конечный получатель только в пределах наблюдаемой выборки.')
    if role == 'coordinator':
        minimum = thresholds['coordinator_betweenness_min']
        return (f'Порог посредничества ≥{minimum:.6g}.' if compact else
                f'Приблизительное посредничество {row.betweenness:.6g} ≥ порога {minimum:.6g}; '
                f'входящих/исходящих контрагентов {row.in_deg}/{row.out_deg}; '
                f'соседей вне сообщества {row.cross_cluster_degree}. Требуются вход и выход, '
                'положительное посредничество и ≥2 внешних соседа либо ≥2 контрагента в каждом направлении. '
                'Это структурная роль, а не доказательство управления другими клиентами.')
    raise ValueError(f'Unknown role: {role}')
