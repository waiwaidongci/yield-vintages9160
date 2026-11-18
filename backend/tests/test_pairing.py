import itertools
import random

from src.services.pairing import candidate_adjacency, essential_pairs, maximum_matching, pair_group


def brute_force(adjacency, right_count):
    """枚举所有配对方案，返回最大配对数与每种最大方案共有的边。"""
    best = [-1]
    common = None

    def walk(u, used):
        nonlocal best, common
        if u == len(adjacency):
            count = len(used)
            edges = set(used)
            if count > best[0]:
                best[0] = count
                common = edges
            elif count == best[0]:
                common &= edges
            return
        walk(u + 1, used)
        for v in adjacency[u]:
            if all(edge[1] != v for edge in used):
                walk(u + 1, used + [(u, v)])

    walk(0, [])
    return best[0], (common or set())


def test_matching_matches_brute_force_on_random_graphs():
    rng = random.Random(20261118)
    for _ in range(400):
        n = rng.randint(0, 6)
        m = rng.randint(0, 6)
        adjacency = [[v for v in range(m) if rng.random() < 0.45] for _ in range(n)]
        pair_left, pair_right, size = maximum_matching(adjacency, m)
        expected_size, expected_common = brute_force(adjacency, m)
        assert size == expected_size
        assert essential_pairs(adjacency, pair_left, pair_right) == expected_common
        # 每个最大配对方案共有的边必然属于返回的最大配对本身。
        common = essential_pairs(adjacency, pair_left, pair_right)
        assert all(pair_left[u] == v for u, v in common)


def test_ambiguous_group_only_keeps_unique_pairs():
    # 底稿投入 100/101 与抽检 100/101 互相可配：两套等价最大方案，任何配对都不唯一。
    drafts = [
        {'id': 1, 'period': '2026-11-03', 'department': 'A', 'shift': '早班', 'input': 100},
        {'id': 2, 'period': '2026-11-03', 'department': 'A', 'shift': '早班', 'input': 101},
    ]
    inspections = [
        {'id': 11, 'batch_no': 'B1', 'period': '2026-11-03', 'department': 'A', 'shift': '早班', 'input': 100},
        {'id': 12, 'batch_no': 'B2', 'period': '2026-11-03', 'department': 'A', 'shift': '早班', 'input': 101},
    ]
    result = pair_group(drafts, inspections, 10)
    assert result['matching_size'] == 2
    assert result['pairs'] == []
    assert {item['reason'].startswith('存在多套等价最大配对方案') for item in result['undetermined']}


def test_group_keeps_unique_pairs_while_ambiguous_cluster_stays_open():
    # 底稿 100/112 与抽检 100/110/122 构成可互换链，任何一套最大方案都不唯一；
    # 底稿 300 与抽检 300 只有彼此可配，因此在每种最大方案中都相同。
    drafts = [
        {'id': 1, 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 100},
        {'id': 2, 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 112},
        {'id': 3, 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 300},
    ]
    inspections = [
        {'id': 11, 'batch_no': 'B1', 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 100},
        {'id': 12, 'batch_no': 'B2', 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 110},
        {'id': 13, 'batch_no': 'B3', 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 122},
        {'id': 14, 'batch_no': 'B4', 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 300},
    ]
    result = pair_group(drafts, inspections, 10)
    assert result['matching_size'] == 3
    assert {(drafts[u]['id'], inspections[v]['id']) for u, v in result['pairs']} == {(3, 14)}
    undetermined_ids = {(item['kind'], item['id']) for item in result['undetermined']}
    assert undetermined_ids == {
        ('draft', 1), ('draft', 2),
        ('inspection', 11), ('inspection', 12), ('inspection', 13),
    }


def test_batch_number_never_drives_pairing():
    drafts = [
        {'id': 1, 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 100},
    ]
    inspections = [
        {'id': 11, 'batch_no': '完全不同的批号', 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 100},
    ]
    result = pair_group(drafts, inspections, 10)
    assert result['pairs'] == [(0, 0)]


def test_cross_shift_and_cross_line_are_not_candidates():
    drafts = [{'id': 1, 'period': 'p', 'department': 'A', 'shift': '早班', 'input': 100}]
    inspections = [
        {'id': 11, 'batch_no': 'B1', 'period': 'p', 'department': 'A', 'shift': '晚班', 'input': 100},
        {'id': 12, 'batch_no': 'B2', 'period': 'p', 'department': 'B', 'shift': '早班', 'input': 100},
    ]
    assert candidate_adjacency(drafts, inspections, 10) == [[]]
    result = pair_group(drafts, inspections, 10)
    assert result['pairs'] == []
    assert all(item['candidate_count'] == 0 for item in result['undetermined'])
