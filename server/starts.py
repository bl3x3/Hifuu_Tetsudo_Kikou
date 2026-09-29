"""Spread starting stations using actual game weights, including the 4-tick express."""
import heapq
from itertools import combinations

MIN_START_DISTANCE = 20
MAX_START_DISTANCE = 40

# Separate regions, with several approaches toward a meeting area. Identities
# remain independently shuffled. The original nearby CSV is design history.
START_GROUPS = [
    ('s030', 's080', 's085'),  # 千葉・益田・高知
    ('s032', 's086', 's094'),  # 横浜・松山・熊本
    ('s026', 's079', 's095'),  # 大宮・出雲市・新八代
    ('s027', 's078', 's091'),  # 高崎・松江・佐賀
    ('s018', 's057', 's083'),  # 仙台・高山・阿波池田
    ('s020', 's069', 's088'),  # 福島・城崎温泉・博多
    ('s033', 's077', 's098'),  # 新横浜・米子・大分
    ('s034', 's084', 's099'),  # 小田原・徳島・別府
    ('s037', 's070', 's073'),  # 松本・新宮・広島
    ('s022', 's038', 's074'),  # いわき・塩尻・新山口
    ('s003', 's041', 's076'),  # 函館・新潟・鳥取
    ('s005', 's016', 's047'),  # 南千歳・秋田・金沢
]


def distances_from(source, adjacency):
    distances, queue = {source: 0}, [(0, source)]
    while queue:
        distance, station = heapq.heappop(queue)
        if distance != distances[station]:
            continue
        for edge, target in adjacency[station]:
            candidate = distance + edge['ticks']
            if candidate < distances.get(target, float('inf')):
                distances[target] = candidate
                heapq.heappush(queue, (candidate, target))
    return distances


def build_templates(stations, adjacency):
    table = {s['id']: s for s in stations}
    distances = {s: distances_from(s, adjacency) for group in START_GROUPS for s in group}
    templates = []
    for index, group in enumerate(START_GROUPS, 1):
        pairs = [distances[a][b] for a, b in combinations(group, 2)]
        if len(set(group)) != 3 or len({table[s]['region'] for s in group}) != 3:
            raise ValueError('Starting stations must belong to three different regions')
        if min(pairs) < MIN_START_DISTANCE or max(pairs) > MAX_START_DISTANCE or max(pairs)/min(pairs) > 1.6:
            raise ValueError(f'Starting group S{index:02} fails travel-distance constraints')
        item = dict(template_id=f'S{index:02}', pair_ab_ticks=pairs[0], pair_ac_ticks=pairs[1],
                    pair_bc_ticks=pairs[2], min_distance_ticks=min(pairs), max_distance_ticks=max(pairs),
                    distance_scope='all_edges_including_express_without_dwell_or_decisions',
                    status='spread_starts_pending_human_playtest')
        for label, station in zip('abc', group):
            item[f'station_{label}_id'] = station
            item[f'station_{label}'] = table[station]['name']
        templates.append(item)
    return templates
