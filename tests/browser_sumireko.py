"""Six phones plus host: automatic encounters, private confirmations and group wins."""

import copy
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor')]
from playwright.sync_api import sync_playwright
from server import game
from server.data import ADJACENCY
from server.web import Server, now_ms


def assert_html_image(page, selector, source):
    page.wait_for_function(
        """({selector, source}) => {
            const images = [...document.querySelectorAll(selector)];
            return images.length > 0 && images.every(image =>
                image.getAttribute('src') === source && image.complete &&
                image.naturalWidth > 0 && !image.hidden);
        }""",
        arg=dict(selector=selector, source=source),
    )


def assert_public_art(page, hat_station, brown_markers=None, anonymous=False):
    """Shared public portraits keep identities private; one aligned layer wears the hat."""
    page.wait_for_function(
        """station => {
            const hat = document.querySelector('image.hat-overlay');
            return document.querySelectorAll('.hat-marker').length === 1 &&
                hat?.closest('.station-player-pill')?.dataset.station === station;
        }""",
        arg=hat_station,
    )
    brown_count = page.locator('.player-marker[data-appearance=brown]').count()
    assert brown_count > 1 if brown_markers is None else brown_count == brown_markers
    assert page.locator('.hat-marker').count() == 1
    assert page.locator('image.hat-overlay').count() == 1
    assert page.locator('.player-marker[data-player]').count() == 0
    assert page.locator('path.hat-fallback').is_hidden()
    markers = page.locator('.player-marker[data-appearance=brown]')
    for marker in markers.all():
        frame = marker.locator('svg.marker-portrait')
        assert frame.count() == 1
        assert frame.get_attribute('width') == '84'
        assert frame.get_attribute('height') == '84'
        assert frame.get_attribute('viewBox') == '0 0 60 60'
        assert frame.evaluate('(element) => getComputedStyle(element).overflow') == 'hidden'
        base = marker.locator('image:not(.hat-overlay)')
        assert base.count() == 1
        assert base.get_attribute('href') == '/assets/avatar-brown.webp'
        for attribute, expected in dict(x='-18', y='-6', width='96', height='96').items():
            assert base.get_attribute(attribute) == expected
        overlay = marker.locator('image.hat-overlay')
        if overlay.count():
            assert overlay.get_attribute('href') == '/assets/hat.webp'
            assert overlay.is_visible()
            for attribute in ('x', 'y', 'width', 'height', 'preserveAspectRatio'):
                assert overlay.get_attribute(attribute) == base.get_attribute(attribute)
        else:
            assert marker.locator('.hat-marker').count() == 0
    purple = page.locator('.player-marker[data-appearance=purple]')
    assert purple.count() == 2
    for marker in purple.all():
        base = marker.locator('image:not(.hat-overlay)')
        assert base.count() == 1
        assert base.get_attribute('href') == '/assets/avatar-unknown.webp'
        assert base.get_attribute('width') == '60'
        assert base.get_attribute('height') == '60'
        assert marker.locator('.hat-marker').count() == 0
    # SVG <image> has no naturalWidth: decode its exact href in the browser.
    assert page.locator('#live-markers').evaluate(
        """async layer => {
            const urls = [...new Set([...layer.querySelectorAll('image')]
                .map(image => image.getAttribute('href')))];
            return (await Promise.all(urls.map(url => new Promise(resolve => {
                const image = new Image();
                image.onload = () => resolve(image.naturalWidth > 0);
                image.onerror = () => resolve(false);
                image.src = url;
            })))).every(Boolean);
        }"""
    )
    assert_html_image(
        page, '.arrival-list .public-avatar[data-appearance=brown] img:not(.avatar-hat)',
        '/assets/avatar-brown.webp',
    )
    # The sightings list repeats the map's single hat on the wearer's portrait.
    assert_html_image(page, '.arrival-list img.avatar-hat', '/assets/hat.webp')
    assert page.locator('.arrival-list img.avatar-hat').count() == 1
    assert page.locator('.arrival-list .hat-note').count() == 1
    tags = page.locator('.arrival-list .sighting-tag').all_text_contents()
    rows = page.locator('.arrival-list > div').count()
    # After Renko first meets a Sumireko, only the two purple seats keep a tag.
    assert len(tags) == len(set(tags)) == (2 if anonymous else rows), tags
    brown_tags = page.locator('.player-marker[data-appearance=brown] .marker-tag').all_text_contents()
    assert all(tag.startswith('×') for tag in brown_tags) if anonymous else all(brown_tags), brown_tags
    assert page.locator('.arrival-list .public-avatar[data-appearance=brown] img').evaluate_all(
        """images => images.every(image => {
            const style = getComputedStyle(image);
            const transform = new DOMMatrixReadOnly(style.transform);
            const origin = style.transformOrigin.split(' ').map(parseFloat);
            return Math.abs(transform.a - 1.6) < .001 &&
                Math.abs(transform.d - 1.6) < .001 &&
                Math.abs(origin[0] - parseFloat(style.width) / 2) < .02 &&
                Math.abs(origin[1] - parseFloat(style.height) / 6) < .02;
        })"""
    )
    assert_html_image(
        page, '.arrival-list .public-avatar[data-appearance=purple] img',
        '/assets/avatar-unknown.webp',
    )


def assert_crowded_station(page, seats):
    page.wait_for_function(
        """() => document.querySelectorAll('.station-player-pill').length === 1 &&
            document.querySelector('.station-player-pill')?.dataset.station === 's060'"""
    )
    assert_public_art(page, 's060', brown_markers=1)
    pill = page.locator('.station-player-pill')
    assert pill.locator('.player-marker').count() == 3
    letters = pill.locator('.player-marker[data-appearance=brown] .marker-tag').text_content()
    assert len(letters) == 4 and letters == ''.join(sorted(letters)), letters
    assert sorted(pill.locator('.player-marker[data-appearance=purple] .marker-tag').all_text_contents()) == sorted(map(str, seats))
    assert pill.locator('.hat-marker').count() == 1
    assert pill.locator('svg.marker-portrait').count() == 3
    geometry = pill.evaluate(
        """element => {
            const bounds = element.querySelector(':scope > rect').getBoundingClientRect().toJSON();
            const frames = [...element.querySelectorAll('svg.marker-portrait')].map(frame => {
                // A nested SVG's bounding rect includes its overflowing source image.
                // A transparent local rect measures the actual clipping viewport instead.
                const probe = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                probe.setAttribute('x', '0');
                probe.setAttribute('y', '0');
                probe.setAttribute('width', '60');
                probe.setAttribute('height', '60');
                probe.setAttribute('fill', 'none');
                probe.setAttribute('stroke', 'none');
                frame.append(probe);
                try {
                    return {
                        rect: probe.getBoundingClientRect().toJSON(),
                        width: frame.getAttribute('width'),
                        height: frame.getAttribute('height'),
                        viewBox: frame.getAttribute('viewBox'),
                        overflow: getComputedStyle(frame).overflow,
                    };
                } finally { probe.remove(); }
            });
            return {bounds, frames};
        }"""
    )
    bounds = geometry['bounds']
    frames = geometry['frames']
    scale = frames[0]['rect']['width'] / 84
    assert scale > 0
    for index, frame in enumerate(frames):
        assert (frame['width'], frame['height'], frame['viewBox'], frame['overflow']) == (
            '84', '84', '0 0 60 60', 'hidden'
        )
        box = frame['rect']
        assert abs(box['width'] - 84 * scale) < .1
        assert abs(box['height'] - 84 * scale) < .1
        assert bounds['left'] - .1 <= box['left'] < box['right'] <= bounds['right'] + .1
        assert bounds['top'] - .1 <= box['top'] < box['bottom'] <= bounds['bottom'] + .1
        if index:
            previous = frames[index - 1]['rect']
            assert box['top'] >= previous['bottom']
            assert abs(box['left'] - previous['left']) < .1
            assert abs(box['top'] - previous['top'] - 92 * scale) < .1


def screenshot_hat_detail(page, output, name):
    """Magnify the real public-map SVG around the wearer, then restore its view."""
    network = page.locator('#network > svg')
    original_view = network.get_attribute('viewBox')
    bounds = page.locator('.station-player-pill:has(.hat-marker)').evaluate(
        '(element) => { const b = element.getBBox(); return {x:b.x,y:b.y,width:b.width,height:b.height}; }'
    )
    detail_style = None
    try:
        detail_style = page.add_style_tag(content='''
            .atlas-heading, .atlas-postcard, #atlas-status,
            #board-panel, #board-dialogue, #dialogue-map-legend {
                visibility: hidden !important;
            }
        ''')
        network.evaluate(
            """(element, b) => element.setAttribute('viewBox',
                `${b.x - 90} ${b.y - 70} ${b.width + 180} ${b.height + 140}`)""",
            bounds,
        )
        network.screenshot(path=str(output / name))
    finally:
        if detail_style is not None:
            detail_style.evaluate('(element) => element.remove()')
        network.evaluate(
            '(element, value) => element.setAttribute("viewBox", value)', original_view
        )


def assert_no_loss_art(page):
    assert page.locator('.collective-ending, .sumireko-loss-cg').count() == 0
    assert page.locator('img[src="/assets/ending-sumireko-loss.webp"]').count() == 0


def main():
    output = ROOT / 'test-results' / 'sumireko'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server = Server('127.0.0.1', 0, Path(directory))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            # Advance at explicit boundaries, while exercising real HTTP/UI actions.
            with patch.object(game, 'advance'), sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                host = browser.new_page(viewport=dict(width=1440, height=1000))
                host.on('pageerror', lambda error: errors.append(str(error)))
                host.goto(base + '/host')
                host.get_by_role('button', name='创建房间', exact=True).click()
                host.locator('.room-code').wait_for()
                code = host.locator('.room-code').inner_text()
                phones = []
                for i in range(6):
                    context = browser.new_context(
                        viewport=dict(width=390, height=844), is_mobile=True, has_touch=True
                    )
                    phone = context.new_page()
                    phone.on('pageerror', lambda error: errors.append(str(error)))
                    phone.goto(f'{base}/join?room={code}')
                    phone.locator('input[name=name]').fill(f'测试旅人{i + 1}')
                    phone.get_by_role('button', name='领取车票').click()
                    phone.get_by_role('button', name='已了解，准备出发').click()
                    phones.append(phone)
                host.wait_for_function(
                    "() => document.querySelector('#host-start-status').textContent.includes('已准备 6/6')"
                )
                host.screenshot(path=str(output / 'lobby-six.png'), full_page=True)
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.locator('[data-action=setup]').click()
                for phone in phones:
                    phone.locator('.identity').wait_for()
                    # Fresh from the lobby the role card stays on screen; the map sits below it.
                    phone.wait_for_timeout(100)
                    assert phone.locator('.identity').bounding_box()['y'] >= 0
                    assert phone.locator('.station-screen .line-map .lm-you').count() == 1
                with server.store.lock:
                    room = server.store.rooms[code]
                    roles = game.by_role(room)
                    r, m, y = (roles[k] for k in ('renko', 'maribel', 'yukari'))
                    extras = [p for p in room['players'] if p['role'] == 'sumireko']
                    for p, station in zip(
                        [r, m, y] + extras, ['s059', 's055', 's088', 's060', 's063', 's001']
                    ):
                        p.update(
                            station=station,
                            lastArrival=dict(station=station, tick=0),
                            lastSighting=dict(station=station, tick=0),
                        )
                        game.deal(room, p)
                    by_id = {p['id']: phones[i] for i, p in enumerate(room['players'])}
                renko, maribel, wearer = (by_id[p['id']] for p in (r, m, extras[0]))
                renko.locator('.hat-status').wait_for()
                assert renko.locator('#hat-target, [data-action=hat]').count() == 0
                for player in extras:
                    assert_html_image(
                        by_id[player['id']], '.identity .role-icon img',
                        '/assets/avatar-sumireko.webp',
                    )
                wearer.screenshot(path=str(output / 'sumireko-private-avatar.png'), full_page=True)
                assert_public_art(host, 's059')
                # Before the first contact every brown letter also names its seat.
                sightings = host.locator('.arrival-list').inner_text()
                for p in [r] + extras:
                    assert f"{p['seat']}号 · {p['name']}" in sightings, sightings
                screenshot_hat_detail(host, output, 'hat-detail-before-exchange.png')
                # Public sightings can coincide without changing real stations or identities.
                with server.store.lock:
                    sightings = {p['id']: copy.deepcopy(p['lastSighting']) for p in room['players']}
                    real_stations = {p['id']: p['station'] for p in room['players']}
                    for player in room['players']:
                        player['lastSighting'] = dict(station='s060', tick=0)
                    room['revision'] += 1
                try:
                    assert_crowded_station(host, (m['seat'], y['seat']))
                    assert {p['id']: p['station'] for p in room['players']} == real_stations
                    host.screenshot(path=str(output / 'six-at-one-station.png'), full_page=True)
                    screenshot_hat_detail(host, output, 'six-at-one-station-detail.png')
                finally:
                    with server.store.lock:
                        for player in room['players']:
                            player['lastSighting'] = sightings[player['id']]
                        room['revision'] += 1
                assert_public_art(host, 's059')
                for phone in phones:
                    assert (
                        phone.locator(
                            '[data-action=request-wait], [data-action=respond-request]'
                        ).count()
                        == 0
                    )
                    assert '请求留站' not in phone.locator('main').inner_text()
                renko.screenshot(path=str(output / 'renko-hat-status.png'), full_page=True)
                maribel.locator('.renko-record').wait_for()
                for phone in phones:
                    phone.locator('[data-action=wait]').click()
                host.locator('[data-action=launch]').click()
                host.locator('[data-action=launch]').wait_for(state='detached')
                with server.store.lock:
                    nodes = ['s059', 's060', 's063']
                    edges = [
                        next(e for e, to in ADJACENCY[a] if to == b)
                        for a, b in zip(nodes, nodes[1:])
                    ]
                    r['queued'] = game.make_card(nodes, edges, 'browser-hat')
                    r['queued']['stops'][1] = False
                    game.depart(room, r)
                    r['trip']['remaining'] = 1
                    with patch.object(game, 'random_value', return_value=0):
                        game.rail_step(room)
                    first_encounter = room['decision']['encounter']['id']
                    frozen_trip = json.loads(json.dumps(r['trip']))
                wearer.locator('.hat-status').wait_for()
                for phone, headline in ((renko, '帽子借出去了'), (wearer, '你戴上了莲子的帽子')):
                    phone.locator('.encounter-card').wait_for()
                    assert headline in phone.locator('.encounter-card').inner_text()
                    assert phone.locator('[data-action=confirm-encounter]').is_enabled()
                for other in (m, y, extras[1], extras[2]):
                    assert by_id[other['id']].locator('.encounter-card').count() == 0
                assert host.locator('.encounter-card').count() == 0
                assert room['decision']['required'] == []
                assert r['station'] is None
                assert not game.private_state(room, r, now_ms())['me']['canAct']
                host.locator('[data-action=continue-force]:disabled').wait_for()
                host.wait_for_function(
                    "() => [...document.querySelectorAll('.player-marker')].some(e => /棕色旅人 × 2（/.test(e.getAttribute('aria-label')))"
                )
                assert room['brownAnonymous']
                assert room['hat']['holder'] == extras[0]['id']
                assert host.locator('.player-marker[data-player]').count() == 0
                sightings = host.locator('.arrival-list').inner_text()
                for p in [r] + extras:
                    assert p['name'] not in sightings
                for p in (m, y):
                    assert f"{p['seat']}号 · {p['name']}" in sightings
                assert host.locator('#board-panel').evaluate(
                    '(e) => e.scrollHeight <= e.clientHeight + 1'
                )
                assert_public_art(host, 's060', anonymous=True)
                screenshot_hat_detail(host, output, 'hat-detail-after-exchange.png')
                wearer.screenshot(path=str(output / 'sumireko-wearing.png'), full_page=True)
                renko.screenshot(
                    path=str(output / 'renko-nonstop-confirmation.png'), full_page=True
                )
                host.screenshot(path=str(output / 'anonymous-map.png'), full_page=True)
                renko.locator('[data-action=confirm-encounter]').click()
                renko.locator('[data-action=confirm-encounter]:disabled').wait_for()
                assert room['tick'] == 1
                assert r['trip'] == frozen_trip
                assert room['decision']['remaining'] is None
                host.locator('[data-action=continue]:disabled').wait_for()
                wearer.reload()
                wearer.locator('.hat-status').wait_for()
                wearer.locator('[data-action=confirm-encounter]:enabled').wait_for()
                assert (
                    int(
                        wearer.locator('[data-action=confirm-encounter]').get_attribute(
                            'data-encounter'
                        )
                    )
                    == first_encounter
                )
                wearer.locator('[data-action=confirm-encounter]').click()
                wearer.locator('[data-action=confirm-encounter]:disabled').wait_for()
                host.locator('[data-action=continue]:enabled').click()
                renko.locator('.encounter-card').wait_for(state='detached')
                assert r['station'] is None
                assert r['trip'] == frozen_trip
                assert room['tick'] == 1
                assert '宇佐见堇子' in wearer.locator('.identity').inner_text()
                assert wearer.evaluate('document.documentElement.scrollWidth <= innerWidth')
                with server.store.lock:
                    room['tick'] = 2
                    m.update(station=extras[0]['station'], corrupted=True)
                    m['lastSighting'] = dict(station=m['station'], tick=2)
                    game.deal(room, m)
                    game.resolve_contacts(room)
                wearer.locator('.hat-status').wait_for(state='detached')
                assert extras[0]['winEligible']
                assert not extras[0]['hatEligible']
                assert room['hat']['holder'] == r['id']
                renko.wait_for_function(
                    "() => document.querySelector('.hat-status')?.textContent.includes('冷却')"
                )
                maribel.wait_for_function(
                    "() => document.querySelector('.renko-record')?.textContent.includes('米原')"
                )
                # Nonstop Kyoto contact never updates Renko's last actual stopped station.
                assert game.private_state(room, m, now_ms())['me']['renkoLastArrival'] == dict(
                    station='s059', tick=0
                )
                before = game.private_state(room, extras[0], now_ms())['me']
                with server.store.lock:
                    y['station'] = extras[0]['station']
                    game.deal(room, y)
                    game.resolve_contacts(room)
                after = game.private_state(room, extras[0], now_ms())['me']
                assert before == after
                assert not extras[0]['winEligible']
                wearer.locator('[data-action=wait]').click()
                wearer.locator('[data-action=wait]:disabled').wait_for()
                assert extras[0]['waitUntil'] == room['tick'] + 2
                assert room['result'] is None
                assert room['phase'] != 'ended'
                for page in [host] + phones:
                    assert page.locator('.ending, .ending-cg').count() == 0
                    assert_no_loss_art(page)
                assert_public_art(host, 's060', anonymous=True)
                wearer.screenshot(path=str(output / 'sumireko-still-playing.png'), full_page=True)
                maribel.screenshot(path=str(output / 'maribel-history.png'), full_page=True)

                # A failed attempt has the exact same two-phone acknowledgment workflow.
                with server.store.lock:
                    assert game.resolve_hat(room, []) is None  # Separate the previous pair.
                    encounter = game.resolve_hat(
                        room, [dict(player=r, station='s060', id=room['serial'] + 1, stop=False)]
                    )
                    assert not encounter['changed']
                    game.open_decision(room, [], encounter=encounter)
                for phone in (renko, wearer):
                    phone.locator('.encounter-card').wait_for()
                    assert '这次没有换帽' in phone.locator('.encounter-card').inner_text()
                    assert '资格' not in phone.locator('.encounter-card').inner_text()
                wearer.screenshot(
                    path=str(output / 'failed-contact-confirmation.png'), full_page=True
                )
                host.locator('[data-action=continue-force]:disabled').wait_for()
                for phone in (renko, wearer):
                    phone.locator('[data-action=confirm-encounter]').click()
                    phone.locator('[data-action=confirm-encounter]:disabled').wait_for()
                host.locator('[data-action=continue]:enabled').click()
                wearer.locator('.encounter-card').wait_for(state='detached')

                # Art slots accept absent files or null without hiding identity or the hat.
                placeholder_context = browser.new_context(viewport=dict(width=390, height=844))
                manifest = json.loads(
                    (ROOT / 'public/assets/manifest.json').read_text(encoding='utf-8')
                )
                manifest['roles']['sumireko'] = '/assets/unpainted-sumireko.png'
                manifest['appearance']['brown'] = None
                placeholder_context.route(
                    '**/assets/manifest.json', lambda route: route.fulfill(json=manifest)
                )
                placeholder_context.add_init_script(
                    'localStorage.setItem('
                    + json.dumps('hifuu-player:' + code)
                    + ', '
                    + json.dumps(extras[0]['token'])
                    + ')'
                )
                placeholder_phone = placeholder_context.new_page()
                placeholder_phone.on('pageerror', lambda error: errors.append(str(error)))
                placeholder_phone.goto(f'{base}/join?room={code}')
                placeholder_phone.locator('.identity').wait_for()
                placeholder_phone.wait_for_function(
                    "() => document.querySelector('.identity .role-icon')?.textContent === '秘'"
                )
                placeholder_screen = placeholder_context.new_page()
                placeholder_screen.set_viewport_size(dict(width=1440, height=1000))
                placeholder_screen.on('pageerror', lambda error: errors.append(str(error)))
                placeholder_screen.goto(f'{base}/screen?room={code}')
                placeholder_screen.locator('.player-marker[data-appearance=brown]').first.wait_for()
                assert (
                    placeholder_screen.locator(
                        '.player-marker[data-appearance=brown] image'
                    ).count()
                    == 0
                )
                assert placeholder_screen.locator(
                    '.player-marker[data-appearance=brown] .marker-avatar-placeholder'
                ).first.is_visible()
                assert placeholder_screen.locator('.hat-marker').count() == 1
                assert placeholder_screen.locator(
                    '.hat-marker path.hat-fallback, path.hat-marker'
                ).first.is_visible()
                placeholder_phone.screenshot(
                    path=str(output / 'unpainted-avatar-fallback.png'), full_page=True
                )
                placeholder_screen.screenshot(
                    path=str(output / 'unpainted-map-fallback.png'), full_page=True
                )
                placeholder_context.close()

                with server.store.lock:
                    r.update(station='s059', trip=None, queued=None, departAt=None)
                    m.update(station=r['station'], corrupted=False)
                    y['station'] = 's088'
                    game.deal(room, r)
                    game.deal(room, m)
                    game.deal(room, y)
                    game.terminal_meeting(room)
                host.locator('.ending').wait_for()
                for phone in phones:
                    phone.locator('.ending').wait_for()
                assert '堇子全员共同获胜' in wearer.locator('.ending').inner_text()
                assert '被紫抓到过' in wearer.locator('.ending').inner_text()
                assert sum(p['won'] for p in room['result']['individuals']) == 3
                assert room['result']['sumireko'] == dict(won=True, total=3, remaining=2)
                for page in [host] + phones:
                    assert_html_image(page, '.ending-cg', '/assets/ending-hifuu.webp')
                    assert_no_loss_art(page)
                host.screenshot(path=str(output / 'six-player-ending.png'), full_page=True)
                wearer.screenshot(
                    path=str(output / 'sumireko-caught-collective-win.png'), full_page=True
                )

                # Exercise each final collective loss, then restore the real win for replay.
                with server.store.lock:
                    winning_room = copy.deepcopy(room)
                endings = [
                    ('hifuu', '终于会合，但堇子已全部被紫抓到。', 'hifuu', 'ending-hifuu.webp'),
                    ('yukari', '下一站，在境界的另一侧。', 'yukari_direct', 'ending-yukari.webp'),
                    ('yukari', '她记得约定，却已经改变了归途。', 'yukari_corrupted', 'ending-yukari.webp'),
                    ('draw', '约定的时刻已过，旅人未能相逢。', 'draw', 'ending-timeout.webp'),
                    ('aborted', '主持人中止本局。', 'aborted', 'journey-aborted.webp'),
                ]
                for outcome, reason, key, source in endings:
                    with server.store.lock:
                        room['result'] = None
                        for index, player in enumerate(extras):
                            player['winEligible'] = outcome != 'hifuu' and index > 0
                        m['corrupted'] = key == 'yukari_corrupted'
                        game.end(room, outcome, reason, 's059')
                    for page in [host] + phones:
                        page.locator(f'.ending[data-outcome={key}]').wait_for()
                    assert not room['result']['sumireko']['won']
                    for player in extras:
                        phone = by_id[player['id']]
                        assert phone.locator('.collective-ending').count() == 0
                        assert_html_image(
                            phone, '.ending-cg',
                            '/assets/journey-aborted.webp' if outcome == 'aborted'
                            else '/assets/ending-sumireko-loss.webp',
                        )
                    for page in (host, renko, maribel, by_id[y['id']]):
                        assert_html_image(page, '.ending-cg', '/assets/' + source)
                        if outcome == 'aborted':
                            assert_no_loss_art(page)
                        else:
                            assert_html_image(
                                page, 'figure.collective-ending img.sumireko-loss-cg',
                                '/assets/ending-sumireko-loss.webp',
                            )
                            assert page.locator('figure.collective-ending').count() == 1
                    if outcome == 'aborted':
                        for page in phones:
                            assert_no_loss_art(page)
                        assert '不计胜负' in wearer.locator('.ending').inner_text()
                    wearer.screenshot(
                        path=str(output / f'sumireko-ending-{key}-phone.png'), full_page=True
                    )
                    host.screenshot(
                        path=str(output / f'sumireko-ending-{key}-host.png'), full_page=True
                    )
                with server.store.lock:
                    revision = room['revision']
                    room.clear()
                    room.update(winning_room)
                    room['revision'] = revision + 1
                for page in [host] + phones:
                    page.locator('.ending[data-outcome=hifuu]').wait_for()
                    assert_html_image(page, '.ending-cg', '/assets/ending-hifuu.webp')
                    assert_no_loss_art(page)
                host.locator('[data-action=replay]').click()
                host.locator('#replay-caption').wait_for()
                host.locator('[data-action=rematch]').click()
                for phone in phones:
                    phone.get_by_role('button', name='已了解，准备出发').wait_for()
                assert len(room['players']) == 6
                assert room['hat'] is None
                assert all(
                    'hatEligible' not in p and 'winEligible' not in p for p in room['players']
                )
                assert not errors, errors
                report = dict(
                    status='passed',
                    clients=7,
                    checks=[
                        'six-seat lobby',
                        'manual presets and wait requests removed',
                        'supplied Sumireko portrait loaded on all three private phones',
                        'shared public brown portrait and one aligned transparent hat before and after exchange',
                        'six same-station sightings merge four brown travelers and retain two nonoverlapping purple seats',
                        'missing avatar and null appearance retain readable fallbacks and hat overlay',
                        'brown letters and seats public until the first contact',
                        'automatic nonstop exchange',
                        'private success and failure confirmations',
                        'both confirmations required even for host',
                        'no forced stop or new route choice',
                        'letterless anonymous public map after contact',
                        'wearer refresh',
                        'immediate return by corrupted Maribel',
                        'last-stop-only record',
                        'secret Yukari disqualification',
                        'disqualified player still acts',
                        'capture during play never reveals final loss art',
                        'collective Sumireko victory including caught members',
                        'final Sumireko loss art for hifuu, both Yukari endings and draw',
                        'host and other phones retain primary ending CG plus collective loss illustration',
                        'aborted game uses neutral ending art and does not count as a loss',
                        'six-player replay',
                        'clean rematch',
                        'no JS exceptions',
                    ],
                )
                (output / 'report.json').write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
                )
                print(json.dumps(report, ensure_ascii=False))
                browser.close()
        finally:
            server.store.stopped.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
