"""Authoritative game engine. Journal wall timestamps never drive game rules."""

import copy
import hashlib
import secrets
from .data import ADJACENCY, TEMPLATES, EDGE_BY_ID, EDGE_LINE, station_lines
from .dialogue import candidates, public_dialogues
from . import journal
from .settings import RULES, MESSAGES

DIALOGUE_RULES = RULES['dialogue']
EVENT_RULES = RULES['events']
HEARTBEAT_TIMEOUT_MS = 5000
RECONNECT_RESUME_MS = 3000
JOURNEY_LIMIT_TICKS = 48 * 60 // 15
DIALOGUE_START_TICK = DIALOGUE_RULES[
    'start_tick'
]  # Day 2 00:00; the journey starts at day 1 16:00.
DISCONNECT_TIMEOUT_MS = 90000
AUTO_CONTINUE_MS = 3000


class StaleAction(ValueError):
    """The phone must refresh its offer, never retry the previous destination."""


def random_value(room, key):
    """Deterministic draw keyed by room seed and event, reproducible in the journal."""
    return (
        int.from_bytes(hashlib.sha256(f"{room['seed']}:{key}".encode()).digest()[:4], 'big')
        / 4294967296
    )


def shuffled(room, values, key):
    return [
        v
        for _, _, v in sorted(
            (random_value(room, f'{key}:{i}'), i, v) for i, v in enumerate(values)
        )
    ]


def create_room(code, now):
    return dict(
        code=code,
        hostToken=secrets.token_hex(24),
        seed=secrets.token_hex(24),
        phase='lobby',
        players=[],
        tick=0,
        created=now,
        updated=now,
        hostSeen=now,
        revision=0,
        events=[],
        arrivals=[],
        requests=[],
        serial=0,
        railAccum=0,
        tickMs=RULES['tick_ms'],
        decision=None,
        technical=None,
        technicalUsed=0,
        result=None,
        template='random',
        attempts={},
        dialogueEnabled=False,
        dialogueProgress={},
        dialogues=[],
        journal=journal.new_clock(),
    )


def set_dialogue_enabled(room, enabled):
    if type(enabled) is not bool:
        raise ValueError('旅途回声开关必须为开启或关闭。')
    if room['phase'] == 'ended':
        raise ValueError('本局已结束，请在下一局设置旅途回声。')
    if room.get('dialogueEnabled', False) != enabled:
        room['dialogueEnabled'] = enabled
        log(room, 'dialogue_setting', enabled=enabled)


def resolve_dialogues(room, entries, roller=None):
    """Resolve each arrival once, sharing probability and cooldown across quote variants."""
    if (
        room['result']
        or not room.get('dialogueEnabled')
        or room['tick'] < DIALOGUE_START_TICK
        or room['technical']
        or room['phase'] not in ('running', 'decision')
    ):
        return
    roller = roller or (lambda key: random_value(room, key))
    handled = set()
    for entry in sorted(entries, key=lambda e: e['id']):
        p = entry['player']
        if p['id'] in handled:
            continue
        matches = candidates(p, entry)
        if not matches:
            continue
        progress = room.setdefault('dialogueProgress', {}).setdefault(
            str(p['id']), dict(groups=[], count=0, misses=0, lastTick=None)
        )
        if progress['count'] >= DIALOGUE_RULES['max_per_player'] or (
            progress['lastTick'] is not None
            and room['tick'] - progress['lastTick'] < DIALOGUE_RULES['cooldown_ticks']
        ):
            continue
        # Never replay an arrival, including one overlapping two memory groups.
        unique = f"dialogue:{p['id']}:{entry['id']}"
        if unique in room['attempts']:
            continue
        available = [(key, q) for key, q in matches if q['group'] not in progress['groups']]
        if not available:
            continue
        key, quote = available[0]
        handled.add(p['id'])
        room['attempts'][unique] = True
        progress['groups'].append(quote['group'])
        probabilities = DIALOGUE_RULES['first_probabilities']
        probability = (
            probabilities[min(progress['misses'], len(probabilities) - 1)]
            if not progress['count']
            else DIALOGUE_RULES['repeat_probability']
        )
        value = roller(unique)
        success = value < probability
        log(
            room,
            'roll',
            event='dialogue',
            player=p['id'],
            group=quote['group'],
            value=value,
            probability=probability,
            success=success,
        )
        if not success:
            progress['misses'] += 1
            continue
        # Choose a variant only after the group's single probability check succeeds.
        # A separate deterministic key keeps the success roll independent of pool size.
        variants = [(k, q) for k, q in available if q['group'] == quote['group']]
        if len(variants) > 1:
            key, quote = variants[int(roller(f'{unique}:quote') * len(variants))]
        progress['count'] += 1
        progress['lastTick'] = room['tick']
        log(room, 'dialogue', player=p['id'], quote=key)
        room.setdefault('dialogues', []).append(
            dict(id=room['serial'], tick=room['tick'], player=p['id'], quote=key)
        )


def log(room, kind, **fields):
    room['serial'] += 1
    room['events'].append(
        dict(id=room['serial'], tick=room['tick'], type=kind, **journal.event_time(room), **fields)
    )
    room['revision'] += 1


def note(room, player, text):
    room['serial'] += 1
    player['messages'].append(dict(id=room['serial'], tick=room['tick'], text=text))
    player['messages'] = player['messages'][-30:]
    room['revision'] += 1


def join(room, name, now):
    if room['phase'] != 'lobby' or len(room['players']) >= 3:
        raise ValueError('房间已锁定或已满，请观看大屏。')
    name = str(name or '').strip()[:12]
    if not name:
        raise ValueError('请填写旅人昵称。')
    seat = next(
        n
        for n in (1, 2, 3)
        if n not in {p.get('seat', i + 1) for i, p in enumerate(room['players'])}
    )
    player = dict(
        id=max((p['id'] for p in room['players']), default=0) + 1,
        seat=seat,
        name=name,
        token=secrets.token_hex(24),
        seen=now,
        ready=False,
        messages=[],
        lastSeq=0,
        requestUsed=False,
    )
    room['players'].append(player)
    room['revision'] += 1
    return player


def make_card(nodes, edges, key):
    return dict(
        id=f"{key}:" + '.'.join(e['id'] for e in edges),
        nodes=nodes,
        edges=[e['id'] for e in edges],
        segments=[e['ticks'] for e in edges],
        ticks=sum(e['ticks'] for e in edges) + len(nodes) - 2,
        name=(
            '卯酉东海道特急'
            if edges[0]['special']
            else ('区间旅行' if len(edges) == 1 else '联程旅行')
        ),
        lines=list(dict.fromkeys(e['name'] for e in edges)),
        transferNotes=list(
            dict.fromkeys(
                f"{e['name']}：{e['via'] or e['transfer']}"
                for e in edges
                if e['via'] or '换乘' in e['transfer']
            )
        ),
        stops=[True] * len(nodes),
    )


def line_offer(line, origin, key):
    card = make_card(line['nodes'][:], [EDGE_BY_ID[eid] for eid in line['edges']], key)
    card.update(lineId=line['id'], name=line['name'], color=line['color'], origin=origin)
    return card


def select_destination(offer, origin, destination):
    if destination == origin or destination not in offer['nodes'] or origin not in offer['nodes']:
        raise ValueError('请选择抽到线路上当前站以外的站点。')
    start, finish = offer['nodes'].index(origin), offer['nodes'].index(destination)
    direction = 1 if finish > start else -1
    indices = list(range(start, finish + direction, direction))
    nodes = [offer['nodes'][i] for i in indices]
    edges = [EDGE_BY_ID[offer['edges'][min(a, b)]] for a, b in zip(indices, indices[1:])]
    card = make_card(nodes, edges, 'selected')
    card.update(
        id=offer['id'],
        name=offer['name'],
        lineId=offer.get('lineId'),
        color=offer.get('color', EDGE_LINE[edges[0]['id']]['color']),
        origin=origin,
    )
    return card


def deal(room, player):
    player['deal'] = player.get('deal', 0) + 1
    key = f"{player['id']}:{player['deal']}"
    lines = station_lines(player['station'])
    previous = player.get('hand', [{}])[0].get('lineId') if player.get('hand') else None
    alternatives = [line for line in lines if line['id'] != previous]
    line = shuffled(room, alternatives or lines, f'line:{key}')[0]
    player['hand'] = [line_offer(line, player['station'], key)]


def open_decision(room, players, initial=False):
    room['serial'] += 1
    room['decision'] = dict(
        id=room['serial'],
        required=[p['id'] for p in players],
        choices={},
        remaining=None,
        initial=initial,
        request=None,
        hostPaused=False,
    )
    if not initial:
        room['phase'] = 'decision'
    room['revision'] += 1
    log(
        room,
        'decision_open',
        batch=room['decision']['id'],
        initial=initial,
        required=[p['id'] for p in players],
    )


def setup(room, template, now):
    if (
        room['phase'] != 'lobby'
        or len(room['players']) != 3
        or any(not p['ready'] or now - p['seen'] >= HEARTBEAT_TIMEOUT_MS for p in room['players'])
    ):
        raise ValueError('需要三名已连接且准备好的玩家。')
    chosen = (
        shuffled(room, TEMPLATES, 'template')[0]
        if template == 'random'
        else next((t for t in TEMPLATES if t['template_id'] == template), None)
    )
    if not chosen:
        raise ValueError('无效起点组合。')
    room['updated'] = now
    if not room.get('journal'):
        room['journal'] = journal.new_clock()
    journal.start(room)
    roles = shuffled(room, ['renko', 'maribel', 'yukari'], 'roles')
    starts = shuffled(room, [chosen[f'station_{c}_id'] for c in 'abc'], 'starts')
    room['template'] = chosen['template_id']
    for i, p in enumerate(room['players']):
        p.update(
            role=roles[i],
            corrupted=False,
            station=starts[i],
            lastArrival=dict(station=starts[i], tick=0),
            trip=None,
            queued=None,
            departAt=None,
            waitUntil=0,
            holdUntil=0,
            protectionUntil=0,
            baseChanges=1,
            bonusChanges=0,
            naraUsed=False,
            hand=[],
            deal=0,
            actionVersion=0,
            requestUsed=False,
        )
        deal(room, p)
        log(room, 'start', player=p['id'], station=p['station'], role=p['role'])
    room['phase'], room['updated'] = 'briefing', now
    open_decision(room, room['players'], initial=True)


def launch(room, now, force=False):
    if room['phase'] != 'briefing':
        raise ValueError('当前不能发车。')
    if any(now - p['seen'] >= HEARTBEAT_TIMEOUT_MS for p in room['players']):
        raise ValueError('请等待三名玩家恢复连接。')
    fill_missing_choices(room, force)
    room['updated'] = now
    if room.get('journal'):
        room['journal']['launchedAt'] = journal.timestamp(room)
    finish_decision(room, trigger='launch')
    log(room, 'launch')


def continue_decision(room, force=False):
    if room['phase'] != 'decision' or not room.get('decision'):
        raise ValueError('当前没有待结束的讨论。')
    if room['technical']:
        raise ValueError('连接恢复中，暂时不能继续行车。')
    missing = fill_missing_choices(room, force)
    finish_decision(room)
    log(room, 'decision_continue', forced=bool(missing))


def fill_missing_choices(room, force):
    batch = room['decision']
    missing = [pid for pid in batch['required'] if str(pid) not in batch['choices']]
    if missing and not force:
        raise ValueError('请等待所有需要选路的玩家确认，或选择未确认者留站并继续。')
    if missing:
        for pid in missing:
            batch['choices'][str(pid)] = dict(kind='wait', automatic=True, hostForced=True)
            p = next(p for p in room['players'] if p['id'] == pid)
            log_choice(room, p, batch['choices'][str(pid)], source='host')
        if batch.get('request') and batch['request'].get('status') == 'pending':
            batch['request']['status'] = 'expired'
    return missing


def schedule_continue(room):
    batch = room['decision']
    if (
        batch
        and not batch['initial']
        and not batch.get('hostPaused')
        and batch['remaining'] is None
        and all(str(pid) in batch['choices'] for pid in batch['required'])
    ):
        batch['remaining'] = AUTO_CONTINUE_MS


def pause_decision(room, paused):
    if room['phase'] != 'decision' or type(paused) is not bool:
        raise ValueError('只能暂停或恢复当前停站讨论。')
    batch = room['decision']
    batch['hostPaused'] = paused
    batch['remaining'] = None
    if not paused:
        schedule_continue(room)
    log(room, 'decision_pause', paused=paused)


def can_act(room, p):
    if room['decision'] and str(p['id']) in room['decision']['choices']:
        return False
    if not p.get('station') or p.get('trip') or p.get('queued'):
        return False
    if room['decision'] and p['id'] in room['decision']['required']:
        return True
    return room['tick'] >= max(p['waitUntil'], p['holdUntil'])


def can_stop(room, player):
    return (
        room['phase'] in ('running', 'decision')
        and not room['technical']
        and bool(player.get('trip'))
        and not player['trip'].get('stopRequested')
    )


def action(room, p, command):
    if room['technical']:
        raise ValueError('连接恢复中，暂时不能操作。')
    if room['phase'] not in ('briefing', 'running', 'decision'):
        raise ValueError('当前不能选择行程。')
    seq = command.get('seq')
    if type(seq) is not int or seq < 1:
        raise ValueError('无效操作序号。')
    if seq <= p['lastSeq']:
        return
    if command.get('version') != p['actionVersion'] or command.get('batch') != (
        room['decision']['id'] if room['decision'] else None
    ):
        raise StaleAction('行动窗口已更新，请使用最新行程。')
    kind = command.get('kind')
    if kind == 'request_wait':
        batch = room['decision']
        if room['phase'] != 'decision' or not batch or batch.get('initial'):
            raise ValueError('只有到站讨论时才能发起请求。')
        if batch.get('request'):
            raise ValueError('本轮已经有一项请求。')
        if p.get('requestUsed'):
            raise ValueError('本局的请求机会已经用过。')
        target_id = command.get('target')
        if type(target_id) is not int or target_id == p['id'] or target_id not in batch['required']:
            raise ValueError('请选择本轮仍待确认的另一名玩家。')
        if str(target_id) in batch['choices']:
            raise ValueError('该玩家已经确认，不能再请求留站。')
        room['serial'] += 1
        request = dict(
            id=room['serial'],
            tick=room['tick'],
            requester=p['id'],
            target=target_id,
            status='pending',
        )
        batch['request'] = request
        room['requests'].append(request)
        room['requests'] = room['requests'][-24:]
        p['requestUsed'] = True
        p['lastSeq'] = seq
        room['revision'] += 1
        log(room, 'request_wait', request=request['id'], player=p['id'], target=target_id)
        return
    if kind == 'respond_request':
        batch = room['decision']
        request = batch.get('request') if batch else None
        if (
            room['phase'] != 'decision'
            or not request
            or request.get('status') != 'pending'
            or request.get('target') != p['id']
        ):
            raise ValueError('当前没有需要你回应的留站请求。')
        if str(p['id']) in batch['choices']:
            raise ValueError('你已经确认了本轮行动。')
        accepted = command.get('accept')
        if type(accepted) is not bool:
            raise ValueError('请选择接受或拒绝。')
        request['status'] = 'accepted' if accepted else 'declined'
        if accepted:
            batch['choices'][str(p['id'])] = dict(kind='wait', request=request['id'])
            log_choice(room, p, batch['choices'][str(p['id'])], source='request')
        p['lastSeq'] = seq
        room['revision'] += 1
        log(room, 'request_response', request=request['id'], player=p['id'], accepted=accepted)
        schedule_continue(room)
        return
    if kind == 'stop':
        if not can_stop(room, p):
            raise ValueError('当前不能申请下一站下车。')
        trip = p['trip']
        if command.get('card') != trip['card']['id'] or command.get('segment') != trip['segment']:
            raise StaleAction('列车已进入新的区间，请确认当前下一站后重新申请下车。')
        trip['stopRequested'] = True
        p['lastSeq'] = seq
        p['actionVersion'] += 1
        log(
            room,
            'stop_requested',
            player=p['id'],
            station=trip['card']['nodes'][trip['segment'] + 1],
        )
        return
    if not can_act(room, p):
        raise ValueError('当前行程已锁定，或尚未到可出发时刻。')
    if kind == 'change':
        if room['tick'] < max(p['holdUntil'], p['waitUntil']):
            raise ValueError('停留期间不能使用改签。')
        if p['baseChanges'] + p['bonusChanges'] < 1:
            raise ValueError('没有可用改签次数。')
        link = next(
            ((e, to) for e, to in ADJACENCY[p['station']] if e['id'] == command.get('edge')), None
        )
        if not link:
            raise ValueError('只能改签本站合法相邻线路。')
        index = command.get('slot')
        if type(index) is not int or index < 0 or index >= len(p['hand']):
            raise ValueError('请选择当前线路进行改签。')
        line = EDGE_LINE[link[0]['id']]
        if p['hand'][index].get('lineId') == line['id']:
            raise ValueError('已经是这条线路，无需改签。')
        room['serial'] += 1
        old_line = p['hand'][index].get('lineId')
        ticket_type = 'bonus' if p['bonusChanges'] else 'base'
        p['hand'][index] = line_offer(line, p['station'], f"change:{room['serial']}")
        p['bonusChanges' if p['bonusChanges'] else 'baseChanges'] -= 1
        if room['decision']:
            room['decision']['choices'].pop(str(p['id']), None)
        p['actionVersion'] += 1
        p['lastSeq'] = seq
        room['revision'] += 1
        log(
            room,
            'change',
            player=p['id'],
            station=p['station'],
            fromLine=old_line,
            toLine=line['id'],
            ticketType=ticket_type,
        )
        return
    if kind == 'wait':
        choice = dict(kind='wait')
    elif kind == 'travel':
        card = next((c for c in p['hand'] if c['id'] == command.get('card')), None)
        if not card:
            raise ValueError('此车票已失效。')
        choice = dict(
            kind='travel', card=select_destination(card, p['station'], command.get('destination'))
        )
    else:
        raise ValueError('未知行动。')
    p['lastSeq'] = seq
    if room['decision']:
        batch = room['decision']
        batch['choices'][str(p['id'])] = choice
        schedule_continue(room)
        room['revision'] += 1
    else:
        apply_choice(room, p, choice, next_boundary=True)
        p['actionVersion'] += 1
    log_choice(room, p, choice)


def log_choice(room, p, choice, source='player'):
    card = choice.get('card', {})
    log(
        room,
        'choice_submitted',
        player=p['id'],
        station=p['station'],
        choice=choice['kind'],
        source=source,
        batch=room['decision']['id'] if room['decision'] else None,
        destination=card.get('nodes', [None])[-1],
        line=card.get('lineId'),
        plannedTicks=card.get('ticks'),
        route=card.get('nodes'),
    )


def apply_choice(room, p, choice, next_boundary=False):
    if choice['kind'] == 'wait':
        p['waitUntil'] = room['tick'] + 2
        return
    p['queued'] = choice['card']
    p['departAt'] = max(room['tick'] + int(next_boundary), p['waitUntil'], p['holdUntil'])
    if not next_boundary and p['departAt'] <= room['tick']:
        depart(room, p)


def finish_decision(room, trigger='host'):
    batch = room['decision']
    for p in room['players']:
        choice = batch['choices'].get(
            str(p['id']), dict(kind='wait') if p['id'] in batch['required'] else None
        )
        if choice:
            apply_choice(room, p, choice)
            p['actionVersion'] += 1
    room['decision'] = None
    room['phase'] = 'running'
    room['revision'] += 1
    log(room, 'decision_close', batch=batch['id'], trigger=trigger)


def depart(room, p):
    if p['queued']:
        p['trip'] = dict(card=p['queued'], segment=0, remaining=p['queued']['segments'][0])
        p['queued'] = None
    if not p['trip']:
        return
    trip = p['trip']
    log(
        room,
        'depart',
        player=p['id'],
        station=p['station'],
        to=trip['card']['nodes'][trip['segment'] + 1],
        edge=trip['card']['edges'][trip['segment']],
    )
    p['station'], p['departAt'] = None, None


def end(room, outcome, reason, station=None):
    if room['result']:
        return
    if room.get('journal'):
        room['journal']['endedAt'] = journal.timestamp(room)
    room['result'] = dict(outcome=outcome, reason=reason, station=station, tick=room['tick'])
    room['phase'], room['decision'], room['technical'] = 'ended', None, None
    log(room, 'end', outcome=outcome, reason=reason, station=station)


def by_role(room):
    return {p['role']: p for p in room['players']}


def terminal_meeting(room):
    roles = by_role(room)
    r, m, y = (roles[k] for k in ('renko', 'maribel', 'yukari'))
    if r['station'] and r['station'] == m['station'] and not m['corrupted']:
        end(room, 'hifuu', '这一次，站台上等着你的，确实是她。', r['station'])
    elif r['station'] and (
        r['station'] == y['station'] or (m['corrupted'] and r['station'] == m['station'])
    ):
        end(
            room,
            'yukari',
            (
                '下一站，在境界的另一侧。'
                if r['station'] == y['station']
                else '她记得约定，却已经改变了归途。'
            ),
            r['station'],
        )


def resolve_events(room, entries, roller=None):
    """Apply protection before corruption, then station bonuses and public memories."""
    if room['result']:
        return
    roller = roller or (lambda key: random_value(room, key))
    roles = by_role(room)
    m, r, y = (roles[k] for k in ('maribel', 'renko', 'yukari'))
    in_night = 8 <= room['tick'] < 56

    def attempt(event, entry, probability, once=False):
        unique = f"{event}:{entry['player']['id']}:{entry['id']}"
        if unique in room['attempts'] or (once and event in room['attempts']):
            return False
        room['attempts'][unique] = True
        if once:
            room['attempts'][event] = True
        value = roller(unique)
        success = value < probability
        log(room, 'roll', event=event, player=entry['player']['id'], value=value, success=success)
        return success

    for e in entries:
        if (
            e['stop']
            and e['station'] == 's060'
            and e['player'] is r
            and in_night
            and attempt('kyoto_protection', e, EVENT_RULES['kyoto_protection_probability'], True)
            and not m['corrupted']
        ):
            m['protectionUntil'] = 56
            note(room, m, MESSAGES['kyoto_protection'])
    if (
        m['station']
        and m['station'] == y['station']
        and not m['corrupted']
        and room['tick'] >= m['protectionUntil']
    ):
        m['corrupted'], m['bonusChanges'] = True, 0
        log(room, 'corruption', player=m['id'], station=m['station'])
        note(room, m, MESSAGES['maribel_corrupted'])
        note(room, y, MESSAGES['yukari_corruption_notice'])
    for e in entries:
        p = e['player']
        if (
            e['stop']
            and e['station'] == 's060'
            and p is m
            and in_night
            and attempt('kyoto_dialogue', e, EVENT_RULES['kyoto_dialogue_probability'], True)
        ):
            note(room, r, MESSAGES['kyoto_dialogue'])
        if (
            e['station'] == 's039'
            and (p is m or p is y)
            and attempt('nagano', e, EVENT_RULES['nagano_probability'])
        ):
            m['holdUntil'] = max(m['holdUntil'], room['tick'] + 2)
            note(room, m, MESSAGES['nagano_hold'])
        if (
            e['stop']
            and e['station'] == 's063'
            and (p is r or (p is m and not m['corrupted']))
            and not p['naraUsed']
        ):
            p['naraUsed'] = True
            p['bonusChanges'] += 1
            note(room, p, MESSAGES['nara_bonus'])
            log(room, 'nara_bonus', player=p['id'])
    resolve_dialogues(room, entries, roller)


def rail_step(room):
    """Settle one 15-minute interval, then arrivals, station events, and decisions."""
    if room['result']:
        return
    waiting = [
        player
        for player in room['players']
        if player['station']
        and not player['trip']
        and not player['queued']
        and player['waitUntil'] == room['tick'] + 1
    ]
    # Complete [t,t+1) using t's final hold state, then resolve the t+1 batch.
    moving = [
        p
        for p in room['players']
        if p['trip'] and not p['station'] and room['tick'] >= p['holdUntil']
    ]
    for p in moving:
        p['trip']['remaining'] -= 1
    room['tick'] += 1
    if room['tick'] > JOURNEY_LIMIT_TICKS:
        end(room, 'draw', '旅程已超过48小时。列车驶过约定的时刻，你们终究未能相逢。')
        return
    for p in room['players']:
        if (
            p['station']
            and p['departAt'] is not None
            and p['departAt'] <= room['tick']
            and p['holdUntil'] <= room['tick']
        ):
            depart(room, p)
    entries, terminals = [], []
    for p in moving:
        if p['trip']['remaining'] > 0:
            continue
        trip = p['trip']
        station = trip['card']['nodes'][trip['segment'] + 1]
        terminal = (
            trip.get('stopRequested', False) or trip['segment'] == len(trip['card']['segments']) - 1
        )
        stop = terminal or trip['card']['stops'][trip['segment'] + 1]
        room['serial'] += 1
        entries.append(
            dict(
                id=room['serial'],
                player=p,
                station=station,
                stop=stop,
                edge=trip['card']['edges'][trip['segment']],
            )
        )
        if stop:
            p['station'] = station
            p['lastArrival'] = dict(station=station, tick=room['tick'])
            room['arrivals'].append(
                dict(player=p['id'], station=station, tick=room['tick'], terminal=terminal)
            )
            log(room, 'arrival', player=p['id'], station=station)
        else:
            log(room, 'pass', player=p['id'], station=station)
        if terminal:
            p.update(trip=None, waitUntil=room['tick'], departAt=None)
            deal(room, p)
            p['actionVersion'] += 1
            terminals.append(p)
        else:
            trip['segment'] += 1
            trip['remaining'] = trip['card']['segments'][trip['segment']]
            if stop:
                p['departAt'] = room['tick'] + 1
    terminal_meeting(room)
    if room['result']:
        return
    resolve_events(room, entries)
    for player in waiting:
        deal(room, player)
        player['actionVersion'] += 1
        terminals.append(player)
        log(room, 'wait_expired', player=player['id'], station=player['station'])
    if terminals:
        open_decision(room, terminals)
    room['revision'] += 1


def advance(room, now):
    """Advance monotonic time while respecting decision and connection pauses."""
    elapsed = max(0, now - room['updated'])
    room['updated'] = now
    if room['phase'] not in ('briefing', 'running', 'decision'):
        return
    missing = (
        any(now - p['seen'] >= HEARTBEAT_TIMEOUT_MS for p in room['players'])
        or now - room['hostSeen'] >= HEARTBEAT_TIMEOUT_MS
    )
    if room['phase'] != 'briefing' and (missing or room['technical']):
        if not room['technical']:
            room['technical'] = dict(resumeAt=None)
            room['technicalUsed'] = 0
            log(
                room,
                'technical_pause',
                missingPlayers=[
                    p['id'] for p in room['players'] if now - p['seen'] >= HEARTBEAT_TIMEOUT_MS
                ],
                hostMissing=now - room['hostSeen'] >= HEARTBEAT_TIMEOUT_MS,
            )
        journal.account(room, elapsed, 'technical')
        # Count each uninterrupted outage separately; the recovery grace period
        # is frozen too, but does not consume the disconnected-time allowance.
        if missing:
            room['technicalUsed'] += elapsed
        if room['technicalUsed'] >= DISCONNECT_TIMEOUT_MS:
            end(room, 'aborted', '本次连接中断持续90秒未恢复，本局中止。')
            return
        if missing:
            room['technical']['resumeAt'] = None
        elif room['technical']['resumeAt'] is None:
            room['technical']['resumeAt'] = now + RECONNECT_RESUME_MS
        elif now >= room['technical']['resumeAt']:
            room['technical'] = None
            room['technicalUsed'] = 0
            log(room, 'technical_resume')
        return
    if room['phase'] == 'briefing':
        journal.account(room, elapsed, 'briefing')
        return
    while elapsed > 0 and not room['result']:
        if room['phase'] == 'decision' and room['decision']['remaining'] is None:
            journal.account(
                room, elapsed, 'hostPaused' if room['decision'].get('hostPaused') else 'decision'
            )
            break
        until_rail = (
            room['tickMs'] - room['railAccum'] if room['phase'] == 'running' else float('inf')
        )
        until_decision = (
            room['decision']['remaining'] if room['phase'] == 'decision' else float('inf')
        )
        step = min(elapsed, until_rail, until_decision)
        journal.account(room, step, room['phase'])
        elapsed -= step
        if room['phase'] == 'running':
            room['railAccum'] += step
        elif room['phase'] == 'decision':
            room['decision']['remaining'] -= step
        if room['phase'] == 'running' and room['railAccum'] >= room['tickMs']:
            room['railAccum'] = 0
            rail_step(room)
        if room['result']:
            break
        if (
            room['phase'] == 'decision'
            and room['decision']['remaining'] is not None
            and room['decision']['remaining'] <= 0
        ):
            finish_decision(room, trigger='auto')
        if step == 0:
            break


def public_request(room, request):
    if not request:
        return None
    players = {p['id']: p for p in room['players']}
    requester, target = players.get(request.get('requester')), players.get(request.get('target'))
    if not requester or not target:
        return None
    return dict(
        id=request['id'],
        tick=request['tick'],
        requester=requester['id'],
        requesterSeat=requester['seat'],
        requesterName=requester['name'],
        target=target['id'],
        targetSeat=target['seat'],
        targetName=target['name'],
        status=request.get('status', 'pending'),
    )


def public_state(room, now):
    """Build an explicit public projection; hidden player fields never enter it."""
    done = room['phase'] == 'ended'
    state = dict(
        code=room['code'],
        phase=room['phase'],
        tick=room['tick'],
        railMinutes=round(room['tick'] * 15 + room['railAccum'] / room['tickMs'] * 15, 2),
        decisionRemaining=room['decision']['remaining'] if room['decision'] else None,
        decisionPaused=bool(room['decision'] and room['decision'].get('hostPaused')),
        technical=None,
        readyCount=len(room['decision']['choices']) if room['decision'] else 0,
        players=[],
        arrivals=room['arrivals'][-16:],
        requests=[],
        request=None,
        result=room['result'],
    )
    state['dialogueEnabled'] = room.get('dialogueEnabled', False)
    state['publicDialogues'] = public_dialogues(room, done)
    state['requests'] = [
        item
        for item in (public_request(room, request) for request in room.get('requests', [])[-12:])
        if item
    ]
    state['request'] = (
        public_request(room, room['decision'].get('request')) if room.get('decision') else None
    )
    state['autoWaitCount'] = (
        sum(bool(c.get('automatic')) for c in room['decision']['choices'].values())
        if room['decision']
        else 0
    )
    state['joinAddress'] = room.get('joinAddress')
    state['pendingPlayers'] = (
        [pid for pid in room['decision']['required'] if str(pid) not in room['decision']['choices']]
        if room['decision']
        else []
    )
    if room['technical']:
        resume = room['technical']['resumeAt']
        state['technical'] = dict(
            remaining=max(0, DISCONNECT_TIMEOUT_MS - room['technicalUsed']),
            countdown=max(0, resume - now) if resume else None,
        )
    for i, p in enumerate(room['players']):
        item = dict(
            id=p['id'],
            seat=p.get('seat', i + 1),
            name=p['name'],
            connected=now - p['seen'] < HEARTBEAT_TIMEOUT_MS,
            ready=p['ready'],
            role=p.get('role') if done or p.get('role') == 'renko' else None,
        )
        if p.get('lastArrival'):
            item['lastArrival'] = copy.deepcopy(p['lastArrival'])
        if done:
            item['corrupted'] = p.get('corrupted', False)
        state['players'].append(item)
    if done:
        state['replay'] = copy.deepcopy(
            [
                e
                for e in room['events']
                if e['type'] in ('start', 'depart', 'arrival', 'pass', 'corruption', 'end')
            ]
        )
    return state


def travel_status(room, p):
    """Only the owning phone receives interval progress and arrival countdown."""
    trip = p.get('trip')
    card = trip['card'] if trip else p.get('queued')
    if not card:
        return None
    fraction = room['railAccum'] / room['tickMs']
    if trip:
        index = trip['segment']
        hold = max(0, p['holdUntil'] - room['tick'])
        if p['station']:
            delay = max(0, max(p['departAt'] or room['tick'], p['holdUntil']) - room['tick'])
            progress = 0
        else:
            delay = hold
            progress = (
                1 - (trip['remaining'] - (fraction if not hold else 0)) / card['segments'][index]
            )
        ticks = delay + trip['remaining']
        if not trip.get('stopRequested'):
            ticks += sum(card['segments'][index + 1 :]) + len(card['segments']) - index - 1
        source, target = card['nodes'][index : index + 2]
    else:
        delay = max(0, max(p['departAt'] or room['tick'], p['holdUntil']) - room['tick'])
        ticks, progress = delay + card['ticks'], 0
        source, target = card['nodes'][:2]
    ticks = max(0, ticks - fraction)
    return dict(
        remainingRailMinutes=round(ticks * 15, 1),
        station=p['station'],
        source=source,
        target=target,
        progress=max(0, min(1, progress)),
        destination=target if trip and trip.get('stopRequested') else card['nodes'][-1],
        stopRequested=bool(trip and trip.get('stopRequested')),
        line=card['name'],
        color=card.get('color', '#315c6e'),
        held=p['holdUntil'] > room['tick'],
        paused=bool(room['technical']) or room['phase'] != 'running',
    )


def private_state(room, p, now):
    state = public_state(room, now)
    me = {
        k: copy.deepcopy(p.get(k))
        for k in (
            'id',
            'name',
            'role',
            'corrupted',
            'station',
            'trip',
            'queued',
            'waitUntil',
            'holdUntil',
            'protectionUntil',
            'baseChanges',
            'bonusChanges',
            'hand',
            'messages',
            'lastSeq',
            'requestUsed',
        )
    }
    batch = room['decision']
    choice = batch['choices'].get(str(p['id'])) if batch else None
    me.update(
        seat=next(x['seat'] for x in state['players'] if x['id'] == p['id']),
        autoWait=bool(choice and choice.get('automatic')),
        version=p.get('actionVersion'),
        batch=batch['id'] if batch else None,
        submitted=bool(choice),
        chosen=(choice.get('card', {}).get('id') or 'wait') if choice else None,
        chosenDestination=choice['card']['nodes'][-1] if choice and choice.get('card') else None,
        requestAvailable=bool(
            batch
            and not batch.get('initial')
            and not p.get('requestUsed')
            and not batch.get('request')
        ),
        requestTarget=bool(
            batch
            and batch.get('request')
            and batch['request'].get('target') == p['id']
            and batch['request'].get('status') == 'pending'
        ),
        canAct=not room['technical']
        and room['phase'] in ('briefing', 'running', 'decision')
        and can_act(room, p),
        canStop=can_stop(room, p),
        canChange=not room['technical']
        and can_act(room, p)
        and room['tick'] >= max(p['waitUntil'], p['holdUntil']),
        adjacent=(
            [
                dict(edge=e['id'], to=to, ticks=e['ticks'], name=e['name'])
                for e, to in ADJACENCY[p['station']]
            ]
            if p.get('station') and not p.get('trip')
            else []
        ),
    )
    me['motion'] = travel_status(room, p)
    me['otherPlayersDeciding'] = room['phase'] == 'decision' and p['id'] not in batch['required']
    me['availableLines'] = []
    if p.get('station') and not p.get('trip'):
        for line in station_lines(p['station']):
            edge = next(
                e['id']
                for e, _ in ADJACENCY[p['station']]
                if EDGE_LINE[e['id']]['id'] == line['id']
            )
            index = line['nodes'].index(p['station'])
            neighbors = [
                line['nodes'][i] for i in (index - 1, index + 1) if 0 <= i < len(line['nodes'])
            ]
            me['availableLines'].append(
                dict(
                    id=line['id'],
                    name=line['name'],
                    color=line['color'],
                    edge=edge,
                    start=line['nodes'][0],
                    end=line['nodes'][-1],
                    neighbors=neighbors,
                )
            )
    departure = max(
        room['tick'] + int(room['phase'] == 'running'), p.get('waitUntil', 0), p.get('holdUntil', 0)
    )
    for offer in me['hand'] or []:
        offer['destinations'] = []
        if not p.get('station') or p.get('trip') or p.get('queued'):
            continue
        for station in offer['nodes']:
            if station == p['station']:
                offer['destinations'].append(
                    dict(station=station, current=True, ticks=0, arrivalTick=room['tick'])
                )
            else:
                ticket = select_destination(offer, p['station'], station)
                offer['destinations'].append(
                    dict(
                        station=station,
                        current=False,
                        ticks=ticket['ticks'],
                        arrivalTick=departure + ticket['ticks'],
                    )
                )
    state['me'] = me
    return state
