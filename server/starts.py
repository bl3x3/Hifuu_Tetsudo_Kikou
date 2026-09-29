"""Spread starting stations using actual game weights, including the 4-tick express."""

import heapq
from itertools import combinations

from .settings import STARTS

MIN_START_DISTANCE = STARTS['min_distance_ticks']
MAX_START_DISTANCE = STARTS['max_distance_ticks']
MAX_START_DISTANCE_RATIO = STARTS['max_distance_ratio']
START_GROUPS = STARTS['groups']


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
    for group in START_GROUPS:
        if any(station not in table for station in group):
            raise ValueError(f'config/starts.json: unknown starting station in {group}')
    distances = {s: distances_from(s, adjacency) for group in START_GROUPS for s in group}
    templates = []
    for index, group in enumerate(START_GROUPS, 1):
        pairs = [distances[a][b] for a, b in combinations(group, 2)]
        if len(set(group)) != 3 or len({table[s]['region'] for s in group}) != 3:
            raise ValueError('Starting stations must belong to three different regions')
        if (
            min(pairs) < MIN_START_DISTANCE
            or max(pairs) > MAX_START_DISTANCE
            or max(pairs) / min(pairs) > MAX_START_DISTANCE_RATIO
        ):
            raise ValueError(f'Starting group S{index:02} fails travel-distance constraints')
        item = dict(
            template_id=f'S{index:02}',
            pair_ab_ticks=pairs[0],
            pair_ac_ticks=pairs[1],
            pair_bc_ticks=pairs[2],
            min_distance_ticks=min(pairs),
            max_distance_ticks=max(pairs),
            distance_scope='all_edges_including_express_without_dwell_or_decisions',
            status='spread_starts_pending_human_playtest',
        )
        for label, station in zip('abc', group):
            item[f'station_{label}_id'] = station
            item[f'station_{label}'] = table[station]['name']
        templates.append(item)
    return templates
