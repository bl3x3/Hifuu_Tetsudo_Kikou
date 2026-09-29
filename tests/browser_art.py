"""Art/layout regression against an isolated real server, never live rooms."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor')]
from playwright.sync_api import sync_playwright
from server import game
from server.data import EDGE_LINE, EDGE_BY_ID, ADJACENCY
from server.web import Server, now_ms


def main():
    output = ROOT / 'test-results' / 'art-refresh'
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        server = Server('127.0.0.1', 0, Path(directory))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        room = game.create_room('ARTQA', now_ms())
        for name in ('旅人一的十二字长昵称测试中', '旅人二', '旅人三'):
            game.join(room, name[:12], now_ms())['ready'] = True
        server.store.rooms['ARTQA'] = room

        def update(**values):
            with server.store.lock:
                room.update(**values)
                room['revision'] += 1
                room.update(updated=now_ms(), hostSeen=now_ms())
                for p in room['players']:
                    p['seen'] = now_ms()

        def fits(page):
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.url

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                home = browser.new_page(viewport=dict(width=1920, height=1080))
                home.on('pageerror', lambda e: errors.append(str(e)))
                home.goto(base)
                home.locator('.title-menu').wait_for()
                home.wait_for_function('()=>document.querySelector(".title-art")?.naturalWidth>0')
                home.screenshot(path=str(output / 'title-desktop.png'))
                for width in (320, 390, 768):
                    home.set_viewport_size(dict(width=width, height=844))
                    fits(home)
                home.set_viewport_size(dict(width=390, height=844))
                home.screenshot(path=str(output / 'title-phone.png'))
                home.goto(base + '/join')
                home.locator('#join-form').wait_for()
                home.screenshot(path=str(output / 'entry-phone.png'))
                home.set_viewport_size(dict(width=320, height=700))
                fits(home)
                checks.append('title and player entry: 320–1920 px, no overflow')

                host = browser.new_page(viewport=dict(width=1920, height=1080))
                host.add_init_script('localStorage.setItem("hifuu-host:ARTQA",' + json.dumps(room['hostToken']) + ')')
                host.on('pageerror', lambda e: errors.append(str(e)))
                host.goto(base + '/host?room=ARTQA')
                host.locator('.room-code').wait_for()
                assert host.locator('.join-qr').bounding_box()['width'] >= 180
                host.wait_for_function('()=>document.querySelector(".lobby-stage .title-art")?.naturalWidth>0')
                assert host.locator('.lobby-stage').evaluate('(element)=>element.classList.contains("title-home")')
                assert host.locator('.lobby-stage .title-art').get_attribute('src') == '/assets/title.webp'
                assert host.locator('.map-frame').is_hidden()
                assert host.locator('.role-icon').count() == 0
                host.wait_for_function('()=>Array.from(document.querySelectorAll(".public-avatar img")).length===3 && Array.from(document.querySelectorAll(".public-avatar img")).every(i=>i.naturalWidth>0 && i.src.endsWith("avatar-unknown.webp"))')
                host.screenshot(path=str(output / 'lobby-desktop.png'))
                for width, height in ((320, 700), (390, 844), (560, 800), (1024, 768), (1920, 1080)):
                    host.set_viewport_size(dict(width=width, height=height))
                    fits(host)
                    assert host.locator('[data-action=setup]').bounding_box()['y'] < height - 44
                    if width==390:
                        host.screenshot(path=str(output / 'lobby-phone.png'), full_page=True)
                spectator=browser.new_page(viewport=dict(width=1920,height=1080))
                spectator.on('pageerror', lambda error: errors.append(str(error)))
                spectator.goto(base+'/screen?room=ARTQA')
                spectator.locator('.room-code').wait_for()
                host.locator('#host-settings summary').click()
                host.locator('#template-select').focus()
                update()
                host.wait_for_timeout(900)
                assert host.locator('#host-settings').get_attribute('open') is not None
                assert host.locator('#template-select').evaluate('(e)=>e===document.activeElement')
                checks.append('lobby QR, neutral seats, settings and focus persist')

                with server.store.lock:
                    game.setup(room, 'random', now_ms())
                    room['decision']['remaining'] = 300000
                    for p, role, station in zip(room['players'], ('renko', 'maribel', 'yukari'), ('s060', 's063', 's088')):
                        p.update(role=role, station=station, lastArrival=dict(station=station, tick=0))
                        game.deal(room, p)
                    me = room['players'][0]
                    me['hand'] = [game.line_offer(EDGE_LINE['e068'], 's060', 'art-long-line')]
                update()
                phone = browser.new_page(viewport=dict(width=390, height=844), is_mobile=True, has_touch=True)
                phone.add_init_script('localStorage.setItem("hifuu-player:ARTQA",' + json.dumps(me['token']) + ')')
                phone.on('pageerror', lambda e: errors.append(str(e)))
                phone.goto(base + '/join?room=ARTQA')
                phone.locator('.axis-stop.current').wait_for()
                phone.wait_for_function('()=>document.querySelector(".identity .role-icon img")?.naturalWidth>0')
                assert phone.locator('.identity .role-icon img').get_attribute('src') == '/assets/avatar-renko.webp'
                host.wait_for_function('()=>document.querySelector(".arrival-list .public-avatar img")?.naturalWidth>0')
                assert host.locator('.arrival-list img[src="/assets/avatar-renko.webp"]').count() == 1
                assert host.locator('.arrival-list img[src="/assets/avatar-unknown.webp"]').count() == 2
                assert host.locator('.traveler').first.locator('span').all_text_contents() == ['宇佐见莲子',room['players'][0]['name']]
                assert host.locator('.traveler').first.evaluate('(element)=>getComputedStyle(element.children[0]).fontSize===getComputedStyle(element.children[1]).fontSize')
                assert host.locator('.player-marker image[href="/assets/avatar-renko.webp"]').count() == 1
                assert host.locator('.player-marker image[href="/assets/avatar-unknown.webp"]').count() == 2
                assert host.locator('.player-marker text').all_text_contents() == ['秘','秘','秘']
                host.screenshot(path=str(output / 'map-public-avatars.png'))
                assert phone.locator('.axis-stop').count() == len(me['hand'][0]['nodes'])
                for width in (320, 360, 390, 430):
                    phone.set_viewport_size(dict(width=width, height=844))
                    fits(phone)
                    dock = phone.locator('.destination-actions').bounding_box()
                    assert dock['x'] >= 0 and dock['x'] + dock['width'] <= width + 1
                    assert phone.locator('[data-action=travel]').bounding_box()['height'] >= 44
                phone.set_viewport_size(dict(width=390, height=844))
                phone.screenshot(path=str(output / 'station-viewport.png'))
                phone.locator('.axis-stop:not(:disabled)').last.click()
                selected = phone.locator('.axis-stop.selected').get_attribute('data-destination')
                axis_scroll = phone.locator('.station-axis').evaluate('(e)=>e.scrollTop')
                update()
                phone.wait_for_timeout(900)
                assert phone.locator('.axis-stop.selected').get_attribute('data-destination') == selected
                assert abs(phone.locator('.station-axis').evaluate('(e)=>e.scrollTop') - axis_scroll) <= 1
                phone.locator('[data-action=open-change]').click()
                assert phone.locator('#change-panel').get_attribute('open') is not None
                phone.locator('#private-notebook summary').click()
                phone.locator('#private-notebook summary').focus()
                update()
                phone.wait_for_timeout(900)
                assert phone.locator('#private-notebook').get_attribute('open') is not None
                assert phone.locator('#private-notebook summary').evaluate('(e)=>e===document.activeElement')
                assert phone.locator('#change-panel').get_attribute('open') is not None
                checks.append('all stops, fixed ticket, selection/scroll, change shortcut, notebook persist')

                with server.store.lock:
                    me['station'] = 's023'
                    me['queued'] = game.make_card(['s023', 's060'], [EDGE_BY_ID['e131']], 'art-express')
                    game.depart(room, me)
                update(phase='running', tick=8)
                phone.locator('.moving-screen.is-traveling').wait_for()
                phone.evaluate('window.scenery=document.querySelector(".window-landscape")')
                phone.wait_for_timeout(250)
                before = phone.locator('.window-landscape').evaluate('(e)=>e.getAnimations()[0].currentTime')
                update()
                phone.wait_for_timeout(900)
                assert phone.evaluate('window.scenery===document.querySelector(".window-landscape")')
                after = phone.locator('.window-landscape').evaluate('(e)=>e.getAnimations()[0].currentTime')
                assert after > before + 500, (before, after)
                for mode in ('hold', 'stop', 'decision'):
                    with server.store.lock:
                        me.update(holdUntil=room['tick'] + 2 if mode == 'hold' else 0,
                                  station='s023' if mode == 'stop' else None)
                    update(phase='decision' if mode == 'decision' else 'running')
                    phone.locator('.moving-screen.is-stopped').wait_for()
                    phone.wait_for_timeout(800)
                    assert phone.locator('.window-landscape').evaluate('(e)=>getComputedStyle(e).animationPlayState') == 'paused'
                    t = phone.locator('.window-landscape').evaluate('(e)=>e.getAnimations()[0].currentTime')
                    phone.wait_for_timeout(200)
                    assert abs(phone.locator('.window-landscape').evaluate('(e)=>e.getAnimations()[0].currentTime') - t) < 2
                update(phase='running')
                phone.locator('.moving-screen.is-traveling').wait_for()
                phone.context.set_offline(True)
                phone.locator('#connection.offline').wait_for(timeout=8000)
                assert phone.locator('.window-landscape').evaluate('(e)=>getComputedStyle(e).animationPlayState') == 'paused'
                phone.context.set_offline(False)
                phone.locator('.moving-screen.is-traveling').wait_for(timeout=8000)
                phone.emulate_media(reduced_motion='reduce')
                assert phone.locator('.window-landscape').evaluate('(e)=>getComputedStyle(e).animationName') == 'none'
                phone.emulate_media(reduced_motion='no-preference')
                checks.append('scenery continuity, hold, layover, decision, offline, reduced motion')

                with server.store.lock:
                    nodes=['s059','s060','s063']
                    edges=[next(edge for edge,target in ADJACENCY[source] if target==destination)
                           for source,destination in zip(nodes,nodes[1:])]
                    me.update(station='s059',trip=None,queued=game.make_card(nodes,edges,'art-stop'),holdUntil=0)
                    game.depart(room,me)
                    me['trip']['remaining']=2
                update(phase='running',decision=None,tickMs=60000,railAccum=0)
                phone.wait_for_function('()=>document.querySelector(".journey-countdown .eyebrow")?.textContent.includes("奈良")')
                phone.locator('[data-action=stop]:enabled').wait_for()
                for width in (320,390):
                    phone.set_viewport_size(dict(width=width,height=844))
                    fits(phone)
                    assert phone.locator('[data-action=stop]').bounding_box()['height']>=44
                phone.locator('[data-action=stop]').click()
                phone.locator('[data-action=stop]:disabled').wait_for()
                assert '京都' in phone.locator('.stop-request').inner_text()
                assert '京都' in phone.locator('.journey-countdown').inner_text()
                phone.reload()
                phone.locator('[data-action=stop]:disabled').wait_for()
                phone.screenshot(path=str(output/'phone-stop-requested.png'),full_page=True)
                with server.store.lock:
                    game.rail_step(room)
                    assert me['station'] is None
                    game.rail_step(room)
                    assert me['station']=='s060'
                    assert me['trip'] is None
                update()
                phone.locator('.station-screen').wait_for()
                assert phone.locator('.drawn-line .axis-stop:not(:disabled)').count()>0
                assert phone.locator('[data-action=stop]').count()==0
                phone.screenshot(path=str(output/'phone-stop-arrived.png'),full_page=True)
                checks.append('next-station stop survives refresh, updates destination, completes the current segment and reopens route choices')

                for outcome, reason, key in [('hifuu', '终于会合。', 'hifuu'), ('yukari', '境界的另一侧。', 'yukari_direct'), ('yukari', '她记得另一份约定。', 'yukari_corrupted'), ('draw', '约定的时刻已过，旅人未能相逢。', 'draw'), ('aborted', '主持人中止本局。', 'aborted')]:
                    with server.store.lock:
                        room['result'] = None  # Each fixture is an independent terminal result.
                        game.end(room, outcome, reason, 's060')
                    update()
                    host.locator(f'.ending[data-outcome={key}]').wait_for()
                    phone.locator(f'.ending[data-outcome={key}]').wait_for()
                    assert host.locator('.map-frame').is_hidden()
                    assert host.locator('.ending-art').bounding_box()['width'] > 900
                    assert phone.locator('.ending-art').bounding_box()['height'] > 180
                    fits(phone)
                    expected_cg = {'hifuu':'ending-hifuu.webp', 'yukari_direct':'ending-yukari.webp', 'yukari_corrupted':'ending-yukari.webp', 'draw':'ending-timeout.webp', 'aborted':'journey-aborted.webp'}[key]
                    for page in (phone, host):
                        page.wait_for_function('(file)=>document.querySelector(".ending-cg")?.src.endsWith(file) && document.querySelector(".ending-cg")?.naturalWidth>0', arg=expected_cg)
                        page.wait_for_function('()=>Array.from(document.querySelectorAll(".reveal-list .role-icon img")).length===3 && Array.from(document.querySelectorAll(".reveal-list .role-icon img")).every(i=>i.naturalWidth>0)')
                    host.screenshot(path=str(output / f'ending-{key}.png'))
                    phone.screenshot(path=str(output / f'ending-{key}-phone.png'))
                host.locator('[data-action=replay]').click()
                assert host.locator('.map-frame').is_visible()
                assert host.locator('#scene-stage').is_hidden()
                host.locator('#replay-caption').wait_for()
                host.locator('[data-action=ending]').click()
                assert host.locator('.ending').is_visible()
                assert host.locator('.map-frame').is_hidden()
                checks.append('three supplied CGs, all four avatars, private identities, aborted art and return from replay')

                broken = browser.new_page(viewport=dict(width=390, height=844))
                manifest = json.loads((ROOT / 'public/assets/manifest.json').read_text(encoding='utf-8'))
                manifest['roles']['renko'] = '/assets/missing-avatar.png'
                manifest['endings']['aborted'] = '/assets/missing-cg.png'
                broken.route('**/assets/manifest.json', lambda route: route.fulfill(json=manifest))
                broken.add_init_script('localStorage.setItem("hifuu-player:ARTQA",' + json.dumps(me['token']) + ')')
                broken.goto(base + '/join?room=ARTQA')
                broken.locator('.ending').wait_for()
                broken.wait_for_function('()=>document.querySelector(".ending-cg")?.hidden===true')
                assert broken.locator('.ending-placeholder').is_visible()
                assert broken.locator('.role-icon').first.inner_text() == '秘'
                checks.append('failed CG and avatar requests retain readable fallback')
                host.locator('[data-action=new-room]').click()
                host.locator('.room-code').wait_for()
                next_code=host.locator('.room-code').inner_text()
                assert next_code!='ARTQA'
                spectator.wait_for_url(f'**/screen?room={next_code}')
                spectator.locator('.room-code').wait_for()
                assert spectator.locator('.room-code').inner_text()==next_code
                assert phone.url.endswith('/join?room=ARTQA')
                spectator.screenshot(path=str(output / 'spectator-new-room.png'))
                checks.append('public role avatars, equal identity/name lines, and automatic spectator room switch across browser contexts')
                assert not errors, errors
                browser.close()
            report = dict(status='passed', checks=checks, limitations='Chromium mobile emulation; physical phones and venue projection still need a visual check.')
            (output / 'art-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=True))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == '__main__':
    main()
