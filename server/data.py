"""Load the authored network. Travel times are game estimates, not a timetable."""
import csv
import json
import re
from pathlib import Path
from .starts import build_templates

ROOT = Path(__file__).resolve().parent.parent


def read_csv(name):
    with (ROOT / 'design' / name).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


STATIONS = read_csv('stations_100_proposed.csv')
STATION_BY_ID = {s['id']: s for s in STATIONS}
POSITIONS = json.loads((ROOT / 'design/map_layout_coordinates.json').read_text(encoding='utf-8'))['positions']
EDGES = [dict(id=e['edge_id'], source=e['from_id'], target=e['to_id'], name=e['corridor'],
              ticks=int(e['game_ticks']), transfer=e['transfer_note'], via=e['route_via'],
              special=e['edge_id'] == 'e131') for e in read_csv('edges_100_game_prototype.csv')]
ADJACENCY = {s['id']: [] for s in STATIONS}
for edge in EDGES:
    assert edge['ticks'] > 0
    ADJACENCY[edge['source']].append((edge, edge['target']))
    ADJACENCY[edge['target']].append((edge, edge['source']))

TEMPLATES = build_templates(STATIONS, ADJACENCY)

# Curated game itineraries combine adjacent corridors into useful destination
# choices. Each itinerary must be a single open path, with no invented links.
LINES = []
EDGE_BY_ID = {e['id']: e for e in EDGES}
EDGE_LINE = {}
GAME_LINE_GROUPS = json.loads((ROOT / 'design/game_lines.json').read_text(encoding='utf-8'))['groups']
group_edges = [eid for group in GAME_LINE_GROUPS for eid in group['edges']]
assert len(group_edges) == len(set(group_edges)) and set(group_edges) == set(EDGE_BY_ID), 'Every edge needs exactly one game line'
assert len({g['name'] for g in GAME_LINE_GROUPS}) == len(GAME_LINE_GROUPS), 'Game line names must be unique'
LINE_NAME_OVERRIDES = {eid: group['name'] for group in GAME_LINE_GROUPS for eid in group['edges']}
def line_name(edge):
    return LINE_NAME_OVERRIDES.get(edge['id'], edge['name'])

remaining = {e['id'] for e in EDGES}
while remaining:
    first = EDGE_BY_ID[min(remaining)]
    component, pending = set(), [first]
    while pending:
        e = pending.pop()
        if e['id'] in component:
            continue
        component.add(e['id'])
        for node in (e['source'], e['target']):
            pending.extend(other for other, _ in ADJACENCY[node]
                           if line_name(other) == line_name(first) and other['id'] not in component)
    remaining -= component
    graph = {}
    for eid in sorted(component):
        e = EDGE_BY_ID[eid]
        graph.setdefault(e['source'], []).append((eid, e['target']))
        graph.setdefault(e['target'], []).append((eid, e['source']))
    assert all(len(links) <= 2 for links in graph.values()), 'Branching line needs explicit ordered services'
    ends = sorted(s for s, links in graph.items() if len(links) == 1)
    assert len(ends) == 2, 'Circular line needs an explicit direction policy'
    nodes, ordered_edges = [ends[0]], []
    while True:
        following = [(eid, to) for eid, to in graph[nodes[-1]] if eid not in ordered_edges]
        if not following:
            break
        eid, to = following[0]
        ordered_edges.append(eid)
        nodes.append(to)
    index = len(LINES)
    line = dict(id=f'L{index+1:03}', name=line_name(first), nodes=nodes, edges=ordered_edges,
                color='#8751a8' if first['special'] else f'hsl({(index * 137.508) % 360:.3f} 62% 37%)')
    LINES.append(line)
    EDGE_LINE.update({eid: line for eid in ordered_edges})
LINES_BY_ID = {line['id']: line for line in LINES}
assert len(LINES) == len(GAME_LINE_GROUPS), 'A game line cannot contain disconnected pieces'


def station_lines(station):
    return [line for line in LINES if station in line['nodes']]


def live_map():
    svg = (ROOT / 'design/全国线网_参考图式.svg').read_text(encoding='utf-8')
    svg = svg.replace('红色新干线、蓝色在来线、灰色辅助联程。', '每条游戏线路使用独立颜色，与手机站点轴一致。')
    svg = re.sub(r'<g id="demo-ui">[\s\S]*?</g>', '', svg)
    # Remove the three demonstration player markers, preserving tracks/legends.
    svg = re.sub(r'<g id="demo-tokens">[\s\S]*?</g>', '', svg)
    def recolor(match):
        eid, markup = match.group(1), match.group(0)
        color = EDGE_LINE[eid]['color']
        markup = markup.replace(f'id="{eid}"', f'id="{eid}" style="--route-color:{color}"', 1)
        return re.sub(r'stroke="(?!#ffffff)[^"]+"', f'stroke="{color}"', markup)
    svg = re.sub(r'<g id="(e\d+)"[\s\S]*?</g>', recolor, svg)
    svg = re.sub(r'<rect x="3050" y="1848"[\s\S]*?(?=<text x="3280" y="780")',
        '<rect x="3050" y="1848" width="705" height="268" rx="8" fill="#ffffff"/>'
        '<text x="3080" y="1900" font-size="32" fill="#213644">线路配色</text>'
        '<text x="3080" y="1955" font-size="27" fill="#53768b">不同线路使用不同颜色</text>'
        '<text x="3080" y="2005" font-size="27" fill="#53768b">手机站点轴与大屏线路同色</text>'
        '<text x="3080" y="2055" font-size="27" fill="#8751a8">紫色虚线：卯酉东海道特急 · 1小时</text>'
        '<text x="3050" y="2150" font-size="25" fill="#66889b">全网常驻 · 无圆点的交叉处不换乘</text>', svg)
    return svg
