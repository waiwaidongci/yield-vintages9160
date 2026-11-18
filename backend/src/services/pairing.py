"""抽检单与试制底稿的一对一配对（纯 Python，无数据库依赖）。

配对条件：同一期间、同一试制线、同一班次，且两边投入数相差不超过 max_input_diff。
1. 先求最大基数的一对一配对（全批配得尽量多）；
2. 再求“在每一种最大配对方案里都出现”的配对；
3. 只有这些确定的配对可以进入良率核算，其余记录未定原因。

批号不参与配对：抽检单批号与底稿批号对不上，按批号认领会串批。
"""
from collections import deque

INF = 10 ** 9


def candidate_adjacency(drafts, inspections, max_input_diff):
    """为每张底稿列出候选抽检单下标；只为同期间、同线、同班且投入差合格的单据建边。"""
    slots = {}
    for j, inspection in enumerate(inspections):
        key = (inspection["period"], inspection["department"], inspection["shift"])
        slots.setdefault(key, []).append(j)
    for values in slots.values():
        values.sort(key=lambda j: inspections[j].get("id", j))
    adjacency = []
    for draft in drafts:
        key = (draft["period"], draft["department"], draft["shift"])
        candidates = []
        for j in slots.get(key, ()):
            if abs(draft["input"] - inspections[j]["input"]) <= max_input_diff:
                candidates.append(j)
        adjacency.append(candidates)
    return adjacency


def maximum_matching(adjacency, right_count):
    """迭代增广路求最大基数配对，返回 (pair_left, pair_right, size)。"""
    left_count = len(adjacency)
    pair_left = [-1] * left_count
    pair_right = [-1] * right_count
    size = 0
    for start in range(left_count):
        if pair_left[start] != -1:
            continue
        seen = [False] * right_count
        previous_left = {start: None}
        arrived_by = {}
        queue = deque([start])
        augmented = False
        while queue and not augmented:
            u = queue.popleft()
            for v in adjacency[u]:
                if seen[v]:
                    continue
                seen[v] = True
                mate = pair_right[v]
                if mate == -1:
                    # 沿交替路回填：u 改配 v，前驱底稿接管 u 原本的抽检单。
                    cursor_left, cursor_right = u, v
                    while cursor_left is not None:
                        old_right = pair_left[cursor_left]
                        pair_left[cursor_left] = cursor_right
                        pair_right[cursor_right] = cursor_left
                        if old_right != -1:
                            pair_right[old_right] = -1
                        cursor_right = arrived_by.get(cursor_left)
                        cursor_left = previous_left[cursor_left]
                    size += 1
                    augmented = True
                    break
                if mate not in previous_left:
                    previous_left[mate] = u
                    arrived_by[mate] = v
                    queue.append(mate)
    return pair_left, pair_right, size


def _strongly_connected(nodes, edges, reverse_edges):
    """Kosaraju 强连通分量（迭代版），返回每个节点的分量编号。"""
    seen = [False] * nodes
    order = []
    for start in range(nodes):
        if seen[start]:
            continue
        seen[start] = True
        stack = [(start, 0)]
        while stack:
            node, index = stack[-1]
            if index < len(edges[node]):
                nxt = edges[node][index]
                stack[-1] = (node, index + 1)
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append((nxt, 0))
            else:
                order.append(node)
                stack.pop()
    component = [-1] * nodes
    current = 0
    for start in reversed(order):
        if component[start] != -1:
            continue
        component[start] = current
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in reverse_edges[node]:
                if component[nxt] == -1:
                    component[nxt] = current
                    stack.append(nxt)
        current += 1
    return component


def essential_pairs(adjacency, pair_left, pair_right):
    """返回所有最大配对方案共有的 (底稿下标, 抽检单下标) 集合。

    对最大配对 M 建交替有向图：非匹配边 左→右，匹配边 右→左。匹配边 (u,v) 必然
    出现在每种最大方案中，当且仅当 u、v 不在同一强连通分量、u 不能由空闲左点到达、
    v 不能到达空闲右点。
    """
    left_count = len(adjacency)
    right_count = len(pair_right)
    total = left_count + right_count

    def left(i):
        return i

    def right(j):
        return left_count + j

    edges = [[] for _ in range(total)]
    reverse_edges = [[] for _ in range(total)]
    for u in range(left_count):
        for v in adjacency[u]:
            if pair_left[u] != v:
                edges[left(u)].append(right(v))
                reverse_edges[right(v)].append(left(u))
    for v in range(right_count):
        u = pair_right[v]
        if u != -1:
            edges[right(v)].append(left(u))
            reverse_edges[left(u)].append(right(v))

    component = _strongly_connected(total, edges, reverse_edges)

    reachable = [False] * total
    stack = []
    for u in range(left_count):
        if pair_left[u] == -1:
            reachable[left(u)] = True
            stack.append(left(u))
    while stack:
        node = stack.pop()
        for nxt in edges[node]:
            if not reachable[nxt]:
                reachable[nxt] = True
                stack.append(nxt)

    reaches_free_right = [False] * total
    stack = []
    for v in range(right_count):
        if pair_right[v] == -1:
            reaches_free_right[right(v)] = True
            stack.append(right(v))
    while stack:
        node = stack.pop()
        for nxt in reverse_edges[node]:
            if not reaches_free_right[nxt]:
                reaches_free_right[nxt] = True
                stack.append(nxt)

    essential = set()
    for v in range(right_count):
        u = pair_right[v]
        if u == -1:
            continue
        if component[left(u)] == component[right(v)]:
            continue
        if reachable[right(v)] or reaches_free_right[left(u)]:
            continue
        essential.add((u, v))
    return essential


def pair_group(drafts, inspections, max_input_diff):
    """对同一期间、线、班次的一组底稿/抽检单做配对并标注未定原因。

    drafts / inspections 为业务字段 dict（含 period、department、shift、input）。
    返回 pairs（确定配对）、undetermined（未定记录）、matching_size（最大配对数）。
    """
    adjacency = candidate_adjacency(drafts, inspections, max_input_diff)
    pair_left, pair_right, matching_size = maximum_matching(adjacency, len(inspections))
    essential = essential_pairs(adjacency, pair_left, pair_right)

    pairs = []
    determined_drafts = set()
    determined_inspections = set()
    for u in range(len(drafts)):
        v = pair_left[u]
        if v != -1 and (u, v) in essential:
            pairs.append((u, v))
            determined_drafts.add(u)
            determined_inspections.add(v)
    pairs.sort(key=lambda item: (drafts[item[0]].get("id", item[0]), inspections[item[1]].get("id", item[1])))

    undetermined = []
    for u, draft in enumerate(drafts):
        if u in determined_drafts:
            continue
        candidates = adjacency[u]
        reason = _draft_reason(candidates, pair_left[u], max_input_diff)
        undetermined.append({
            "kind": "draft",
            "id": draft.get("id"),
            "period": draft["period"],
            "department": draft["department"],
            "shift": draft["shift"],
            "input": draft["input"],
            "candidate_ids": [inspections[j].get("id") for j in candidates],
            "candidate_count": len(candidates),
            "reason": reason,
        })

    candidates_by_inspection = [[] for _ in inspections]
    for u in range(len(drafts)):
        for v in adjacency[u]:
            candidates_by_inspection[v].append(drafts[u].get("id"))
    for v, inspection in enumerate(inspections):
        if v in determined_inspections:
            continue
        candidates = candidates_by_inspection[v]
        reason = _inspection_reason(candidates, pair_right[v], max_input_diff)
        undetermined.append({
            "kind": "inspection",
            "id": inspection.get("id"),
            "batch_no": inspection.get("batch_no"),
            "period": inspection["period"],
            "department": inspection["department"],
            "shift": inspection["shift"],
            "input": inspection["input"],
            "candidate_ids": candidates,
            "candidate_count": len(candidates),
            "reason": reason,
        })

    return {
        "pairs": pairs,
        "pair_left": pair_left,
        "pair_right": pair_right,
        "essential": essential,
        "matching_size": matching_size,
        "undetermined": undetermined,
        "determined_drafts": determined_drafts,
        "determined_inspections": determined_inspections,
    }


def _draft_reason(candidates, matched, max_input_diff):
    if not candidates:
        return "同线同班且投入差不超过%d件的抽检单为0张" % max_input_diff
    if matched != -1:
        return "存在多套等价最大配对方案，该底稿可能与候选抽检单互换（候选%d张），未唯一确定" % len(candidates)
    return "该底稿在本轮最大配对中落空；候选抽检单在等价方案中可配给其他底稿（候选%d张）" % len(candidates)


def _inspection_reason(candidates, matched, max_input_diff):
    if not candidates:
        return "同线同班且投入差不超过%d件的底稿为0张" % max_input_diff
    if matched != -1:
        return "存在多套等价最大配对方案，该抽检单可能与候选底稿互换（候选%d张），未唯一确定" % len(candidates)
    return "该抽检单在本轮最大配对中落空；候选底稿在等价方案中可配给其他抽检单（候选%d张）" % len(candidates)
