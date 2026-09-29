"""Exercise memories on an isolated ephemeral server; never touch the running game."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor')]
from playwright.sync_api import sync_playwright
from server import game
from server.web import Server, now_ms
from server.data import ADJACENCY


def main():
    out = ROOT / 'test-results' / 'dialogue'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server = Server('127.0.0.1', 0, Path(directory))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        room = game.create_room('ECHOA', now_ms())
        for name in ('莲子玩家', '<回声旅人>', '第三旅人'):
            game.join(room, name, now_ms())['ready'] = True
        server.store.rooms[room['code']] = room
        errors = []
        # Freeze only this test server's time while retaining live HTTP polling.
        from unittest.mock import patch
        try:
            with patch.object(game, 'advance'), sync_playwright() as pw:
                browser = pw.chromium.launch()
                host = browser.new_page(viewport=dict(width=1920, height=1080))
                host.add_init_script('localStorage.setItem("hifuu-host:ECHOA",'+json.dumps(room['hostToken'])+')')
                host.on('pageerror', lambda error: errors.append(str(error)))
                host.goto(base+'/host?room=ECHOA')
                host.locator('[data-action=dialogue]').click()
                host.wait_for_function('() => document.querySelector("[data-action=dialogue]").getAttribute("aria-pressed")==="true"')
                assert room['dialogueEnabled']
                assert host.locator('.dialogue-map-marker').count() == 0
                with server.store.lock:
                    for p in room['players']:
                        p['seen'] = now_ms()
                    game.setup(room, 'S04', now_ms())
                    r, m, y = room['players']
                    for p, role, station in zip(room['players'], ('renko','maribel','yukari'), ('s001','s060','s088')):
                        p.update(role=role, station=station, lastArrival=dict(station=station,tick=0))
                        game.deal(room, p)
                    room.update(phase='running', decision=None, tick=31)
                host.wait_for_function('() => document.querySelector("[data-clock=rail]")?.textContent.includes("23:45")')
                assert host.locator('.dialogue-map-marker').count() == 0
                assert host.locator('#dialogue-map-legend').is_hidden()
                # A time-only state update must show markers, even without a full redraw.
                with server.store.lock:
                    room['tick'] = 32
                host.wait_for_function('() => document.querySelectorAll(".dialogue-map-marker").length===11')
                assert host.locator('#dialogue-map-legend').is_visible()
                assert '需乘卯酉特急抵达' in host.locator('.dialogue-map-marker[data-station=s023]').get_attribute('aria-label')
                assert '无需停车' in host.locator('.dialogue-map-marker[data-station=s060]').get_attribute('aria-label')
                host.screenshot(path=str(out/'map-markers-midnight.png'))
                with server.store.lock:
                    game.resolve_dialogues(room, [dict(id=1000,player=m,station='s060',stop=True)], lambda _: 0)
                host.locator('#board-dialogue:not([hidden])').wait_for()
                assert '<回声旅人>' in host.locator('#board-dialogue').inner_text()
                assert host.locator('#board-dialogue img').get_attribute('alt') == '身份未公开'
                assert host.locator('#board-dialogue a').count() == 0
                box = host.locator('#board-dialogue').bounding_box()
                assert box and box['y']+box['height'] <= 1080
                assert host.locator('#board-dialogue').evaluate('(e)=>e.scrollHeight<=e.clientHeight')
                with server.store.lock:
                    room['tick'] = 56
                host.wait_for_function('() => document.documentElement.dataset.theme==="day"')
                assert host.locator('.dialogue-map-marker').count() == 11
                host.screenshot(path=str(out/'host-day.png'))
                screen = browser.new_page(viewport=dict(width=1920,height=1080))
                screen.goto(base+'/screen?room=ECHOA')
                screen.locator('#board-dialogue:not([hidden])').wait_for()
                assert screen.locator('.dialogue-map-marker').count() == 11
                assert screen.locator('[data-action=dialogue]').count() == 0
                phone = browser.new_page(viewport=dict(width=320,height=700),is_mobile=True,has_touch=True)
                phone.set_default_timeout(10000)
                phone.add_init_script('localStorage.setItem("hifuu-player:ECHOA",'+json.dumps(m['token'])+')')
                phone.on('pageerror', lambda error: errors.append(str(error)))
                phone.goto(base+'/join?room=ECHOA')
                phone.locator('#dialogue-history summary').click()
                phone.locator('#dialogue-history .dialogue-card').wait_for()
                assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
                phone.locator('#dialogue-history').scroll_into_view_if_needed()
                phone.screenshot(path=str(out/'phone-320.png'))
                with server.store.lock:
                    source, target = 's023', 's060'
                    edge = next(e for e,to in ADJACENCY[source] if to==target)
                    m['station']=source
                    m['queued']=game.make_card([source,target],[edge],'test-express')
                    game.depart(room,m)
                phone.locator('.moving-screen').wait_for()
                assert phone.locator('#dialogue-history').count()==1
                with server.store.lock:
                    room['tick']=104  # Second day 18:00; render the same card at night.
                    game.open_decision(room,[r])
                    game.resolve_dialogues(room,[dict(id=1001,player=y,station='s039',stop=True)],lambda _:0)
                host.wait_for_function('() => document.documentElement.dataset.theme==="night"')
                host.wait_for_function('() => document.querySelector("#board-dialogue").textContent.includes("极乐")')
                host.screenshot(path=str(out/'host-night.png'))
                assert len(room['dialogues'])==2
                # A phone in the submitted view must retain the public history too.
                with server.store.lock:
                    m.update(trip=None,station='s023',queued=None,departAt=None)
                    game.deal(room,m)
                    game.open_decision(room,[m])
                    room['decision']['choices'][str(m['id'])]=dict(kind='wait')
                phone.locator('.submitted-screen').wait_for()
                assert phone.locator('#dialogue-history').count()==1
                host.locator('[data-action=dialogue]').click()
                host.wait_for_function('() => document.querySelector("[data-action=dialogue]").getAttribute("aria-pressed")==="false"')
                host.wait_for_function('() => document.querySelectorAll(".dialogue-map-marker").length===0')
                screen.wait_for_function('() => document.querySelectorAll(".dialogue-map-marker").length===0')
                assert host.locator('#dialogue-map-legend').is_hidden()
                assert len(room['dialogues'])==2
                host.locator('[data-action=dialogue]').click()
                host.wait_for_function('() => document.querySelectorAll(".dialogue-map-marker").length===11')
                with server.store.lock:
                    game.end(room,'aborted','测试结束')
                phone.locator('.ending').wait_for()
                phone.locator('#dialogue-history summary').click() if not phone.locator('#dialogue-history').evaluate('(e)=>e.open') else None
                phone.locator('#dialogue-history a').first.wait_for()
                host.locator('.ending').wait_for()
                assert host.locator('.dialogue-map-marker').count() == 0
                host.locator('#dialogue-history').scroll_into_view_if_needed()
                host.screenshot(path=str(out/'ending-sources.png'))
                assert not errors, errors
                browser.close()
            print('PASS: midnight map markers, toggle visibility, spectator, 320px phone, history, day/night and post-game sources.')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__=='__main__':
    main()
