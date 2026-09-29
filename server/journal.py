"""Per-round clocks and credential-free analysis records. No filesystem side effects."""

import copy
import hashlib
import secrets
from collections import Counter
from datetime import datetime, timedelta
from .data import STATION_BY_ID
from .dialogue import public_dialogues


def wall_now():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


def new_clock():
    return dict(
        roundId=secrets.token_hex(12),
        createdAt=wall_now(),
        startedAt=None,
        launchedAt=None,
        endedAt=None,
        elapsedMs=0,
        phaseMs=dict(briefing=0, running=0, decision=0, hostPaused=0, technical=0),
        timeQuality='complete',
    )


def timestamp(room):
    clock = room.get('journal')
    if not clock:
        return None
    if not clock['startedAt']:
        return wall_now()
    return (
        datetime.fromisoformat(clock['startedAt']) + timedelta(milliseconds=clock['elapsedMs'])
    ).isoformat(timespec='milliseconds')


def start(room):
    if room.get('journal'):
        room['journal']['startedAt'] = timestamp(room)


def account(room, milliseconds, phase):
    clock = room.get('journal')
    if clock and clock['startedAt'] and not clock['endedAt']:
        clock['elapsedMs'] += milliseconds
        clock['phaseMs'][phase] += milliseconds


def event_time(room):
    clock = room.get('journal')
    return dict(
        at=timestamp(room), elapsedMs=clock['elapsedMs'] if clock and clock['startedAt'] else None
    )


def display_time(tick):
    minute = 16 * 60 + tick * 15
    return f'第{int(minute//1440)+1}日 {int(minute%1440//60):02d}:{int(minute%60):02d}'


def round_id(room):
    if room.get('journal'):
        return room['journal']['roundId']
    # Old snapshots have no calendar-time anchor. A stable ID permits safe retries.
    source = f"{room['code']}:{room['created']}:{room.get('seed', '')}"
    return 'legacy-' + hashlib.sha256(source.encode()).hexdigest()[:24]


def record(room):
    if room['phase'] != 'ended':
        raise ValueError('只有结束后的对局可以生成复盘日志。')
    clock = room.get('journal') or {}
    events = copy.deepcopy(room['events'])  # Engine events contain gameplay fields only.
    counts = Counter(e['type'] for e in events)
    started = clock.get('startedAt')
    complete = bool(started and clock.get('timeQuality') == 'complete')
    launch = next((e for e in events if e['type'] == 'launch'), {})
    first = next((e for e in events if e['type'] == 'start'), {})
    elapsed = clock.get('elapsedMs', 0)
    windows = []
    for opened in (e for e in events if e['type'] == 'decision_open'):
        closed = next(
            (e for e in events if e['type'] == 'decision_close' and e['batch'] == opened['batch']),
            None,
        )
        stop = closed.get('elapsedMs') if closed else elapsed if clock else None
        begin = opened.get('elapsedMs')
        windows.append(
            dict(
                batch=opened['batch'],
                initial=opened['initial'],
                tick=opened['tick'],
                required=opened['required'],
                closed=bool(closed),
                durationSeconds=(
                    round((stop - begin) / 1000, 3)
                    if stop is not None and begin is not None
                    else None
                ),
                continuedBy=closed.get('trigger') if closed else None,
                submissions=[
                    dict(
                        player=e['player'],
                        choice=e['choice'],
                        source=e['source'],
                        afterSeconds=(
                            round((e['elapsedMs'] - begin) / 1000, 3)
                            if begin is not None and e.get('elapsedMs') is not None
                            else None
                        ),
                    )
                    for e in events
                    if e['type'] == 'choice_submitted' and e.get('batch') == opened['batch']
                ],
            )
        )
    players = []
    for p in room['players']:
        own = [e for e in events if e.get('player') == p['id']]
        actions = [e for e in own if e['type'] == 'choice_submitted']
        player_start = next((e for e in own if e['type'] == 'start'), {})
        visited = sorted({e['station'] for e in own if e['type'] in ('start', 'arrival', 'pass')})
        players.append(
            dict(
                id=p['id'],
                seat=p.get('seat'),
                name=p['name'],
                role=p.get('role'),
                corrupted=p.get('corrupted', False),
                startStation=player_start.get('station'),
                finalStation=p.get('station'),
                lastArrival=copy.deepcopy(p.get('lastArrival')),
                visitedStations=visited,
                visitedStationCount=len(visited),
                departures=sum(e['type'] == 'depart' for e in own),
                arrivals=sum(e['type'] == 'arrival' for e in own),
                travelChoices=sum(e['choice'] == 'travel' for e in actions) if clock else None,
                waitChoices=sum(e['choice'] == 'wait' for e in actions) if clock else None,
                hostForcedWaits=sum(e.get('source') == 'host' for e in actions) if clock else None,
                changes=sum(e['type'] == 'change' for e in own) if clock else None,
                stopRequests=sum(e['type'] == 'stop_requested' for e in own),
                waitRequests=sum(e['type'] == 'request_wait' for e in own),
                acceptedWaitRequests=sum(
                    e['type'] == 'request_response' and e.get('accepted') for e in own
                ),
                remainingChanges=dict(base=p.get('baseChanges'), bonus=p.get('bonusChanges')),
            )
        )
    phases = {key: round(value / 1000, 3) for key, value in clock.get('phaseMs', {}).items()}
    return dict(
        schemaVersion=2,
        version='0.1.0',
        roundId=round_id(room),
        room=room['code'],
        previousRoom=room.get('previousRoom'),
        previousRoundId=room.get('previousRoundId'),
        template=room.get('template'),
        seed=room.get('seed'),
        settings=dict(
            tickMs=room['tickMs'],
            minutesPerTick=15,
            dialogueEnabled=room.get('dialogueEnabled', False),
            rulesVersion='2026-09-28-flow' if clock else None,
            disconnectTimeoutSeconds=90 if clock else None,
            autoContinueSeconds=3 if clock else None,
            waitMinutes=30 if clock else None,
        ),
        timing=dict(
            createdAt=clock.get('createdAt'),
            startedAt=started,
            launchedAt=clock.get('launchedAt'),
            endedAt=clock.get('endedAt'),
            startDisplayTime=display_time(first.get('tick', 0)) if first else None,
            endDisplayTime=display_time(room['result']['tick']),
            durationSeconds=round(elapsed / 1000, 3) if complete else None,
            launchedDurationSeconds=(
                round((elapsed - launch['elapsedMs']) / 1000, 3)
                if complete and launch.get('elapsedMs') is not None
                else None
            ),
            observedDurationSeconds=round(elapsed / 1000, 3) if started else None,
            phaseSeconds=phases if started else None,
            gameMinutes=room['result']['tick'] * 15,
            timeQuality=(
                clock.get('timeQuality')
                if started
                else 'not-started' if clock else 'legacy-unknown'
            ),
            restartDetectedAt=clock.get('restartDetectedAt'),
            lastObservedAt=timestamp(room) if started else None,
            durationDefinition='从分配身份开始至结局，含首程选路、讨论和技术暂停；不含大厅等待。',
            clockDefinition='带UTC偏移的服务器本地时间；耗时按单调时钟累计，断电离线时间不推算。',
        ),
        summary=dict(
            eventCounts=dict(counts),
            decisionCount=sum(not w['initial'] for w in windows) if clock else None,
            autoContinues=sum(w['continuedBy'] == 'auto' for w in windows) if clock else None,
            hostPauses=sum(
                e['type'] == 'decision_pause' and e.get('paused', False) for e in events
            ),
            disconnectCount=counts['technical_pause'],
            recoveredDisconnectCount=counts['technical_resume'],
            choiceCounts=(
                dict(Counter(e['choice'] for e in events if e['type'] == 'choice_submitted'))
                if clock
                else None
            ),
        ),
        players=players,
        result=copy.deepcopy(room['result']),
        decisionWindows=windows,
        stationNames={key: value['name'] for key, value in STATION_BY_ID.items()},
        events=events,
        publicDialogues=public_dialogues(room, True),
    )
