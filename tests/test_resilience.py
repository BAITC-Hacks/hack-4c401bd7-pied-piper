"""Known graph answers, safe sums, deterministic ranking and no mutation."""
import pandas as pd
import pytest

from backend.core.resilience import compare_removals


def tables(gids, links, scores):
    edges = pd.DataFrame(links, columns=['src', 'dst', 'sum_kzt'])
    nodes = pd.DataFrame({'gid': gids, 'priority_score': scores})
    nodes['in_kzt'] = nodes.gid.map(edges.groupby('dst').sum_kzt.sum()).fillna(0)
    nodes['out_kzt'] = nodes.gid.map(edges.groupby('src').sum_kzt.sum()).fillna(0)
    return nodes, edges


def test_bridge_removal_fragments_survivors_without_mutating_inputs():
    nodes, edges = tables([1, 2, 3, 4, 5], [(1, 2, 10), (2, 3, 20), (3, 4, 30)], [0, 1, .5, 0, 0])
    original_nodes, original_edges = nodes.copy(deep=True), edges.copy(deep=True)
    result = compare_removals(nodes, edges, 1)
    assert result['baseline']['components'] == 2  # Path + existing isolate.
    assert result['baseline']['largest_component'] == 4
    item = result['priority']
    assert item['removed_gids'] == ['2']
    assert item['remaining_nodes'] == 4
    assert item['largest_component'] == 2
    assert item['largest_share'] == .5
    assert item['components'] == 3
    assert item['new_isolates'] == 1  # Existing isolate 5 is not new.
    assert item['disconnected_pairs'] == 2
    assert item['disconnected_pair_share'] == pytest.approx(2 / 3)
    assert item['affected_kzt'] == 30
    assert item['affected_share'] == .5
    assert result['turnover']['removed_gids'] == ['3']
    pd.testing.assert_frame_equal(nodes, original_nodes)
    pd.testing.assert_frame_equal(edges, original_edges)


def test_directional_amounts_count_once_even_between_two_removed_nodes():
    nodes, edges = tables([1, 2, 3], [(1, 2, 10), (2, 1, 20), (2, 2, 5), (2, 3, 30)], [1, .9, 0])
    item = compare_removals(nodes, edges, 2)['priority']
    assert item['affected_edges'] == 4
    assert item['affected_kzt'] == 65
    assert item['affected_share'] == 1
    assert item['disconnected_pair_share'] == 0  # No pair of survivors.


def test_large_string_ids_numeric_ties_and_shuffle_invariance():
    nodes, edges = tables(['9007199254740993', '10', '2'], [('2', '10', 5)], [1, 1, 1])
    result = compare_removals(nodes, edges, 3)
    assert result['priority']['removed_gids'] == ['2', '10', '9007199254740993']
    assert result['turnover']['removed_gids'] == ['2', '10', '9007199254740993']
    assert compare_removals(nodes.sample(frac=1, random_state=7), edges, 3) == result


def test_empty_edgeless_and_full_removal_are_defined():
    nodes, edges = tables([1, 2], [], [1, 0])
    result = compare_removals(nodes, edges, 10)
    assert result['baseline']['isolates'] == 2
    item = result['priority']
    assert item['removed_nodes'] == 2
    assert item['remaining_nodes'] == item['components'] == item['largest_component'] == 0
    assert item['largest_share'] == item['affected_share'] == item['disconnected_pair_share'] == 0
    empty_nodes, empty_edges = tables([], [], [])
    assert compare_removals(empty_nodes, empty_edges)['baseline']['remaining_nodes'] == 0


def test_removing_leaf_does_not_claim_surviving_pairs_disconnected():
    nodes, edges = tables([1, 2, 3], [(1, 2, 10), (2, 3, 10)], [1, .5, 0])
    item = compare_removals(nodes, edges, 1)['priority']
    assert item['largest_component'] == 2
    assert item['disconnected_pair_share'] == 0


@pytest.mark.parametrize('n', [0, -1, 1.5, True])
def test_invalid_removal_count(n):
    nodes, edges = tables([1], [], [1])
    with pytest.raises(ValueError, match='positive integer'):
        compare_removals(nodes, edges, n)
