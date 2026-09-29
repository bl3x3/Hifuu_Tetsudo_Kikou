"""Targeted regressions from the human playtest; isolated temporary HTTP server."""
import json
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
from playwright.sync_api import sync_playwright
from server import game
from server.data import EDGE_LINE
from server.web import Server,now_ms

def main():
    out=ROOT/'test-results'/'human-fixes';out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server=Server('127.0.0.1',0,Path(directory))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        room=game.create_room('HUMAN',now_ms())
        for name in ('A','B','C'):game.join(room,name,now_ms())['ready']=True
        game.setup(room,'S04',now_ms())
        p=room['players'][0]
        server.store.rooms[room['code']]=room
        stop=threading.Event()
        def presence():
            while not stop.wait(.1):
                with server.store.lock:
                    room['hostSeen']=now_ms()
                    for player in room['players']:player['seen']=now_ms()
        heartbeat=threading.Thread(target=presence,daemon=True);heartbeat.start()
        errors=[];results=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                host=browser.new_page(viewport=dict(width=1920,height=1080))
                host.goto(base+'/screen?room=HUMAN')
                for width,height in ((320,568),(390,844)):
                    with server.store.lock:
                        room.update(phase='briefing',tick=0,railAccum=0,updated=now_ms())
                        p.update(station='s023',trip=None,queued=None,waitUntil=0,holdUntil=0,
                                 lastArrival=dict(station='s023',tick=0),lastSeq=0)
                        p['hand']=[game.line_offer(EDGE_LINE['e016'],'s023','human'+str(width))]
                        game.open_decision(room,room['players'],initial=True)
                    phone=browser.new_page(viewport=dict(width=width,height=height),is_mobile=True,has_touch=True)
                    phone.on('pageerror',lambda e:errors.append(str(e)))
                    phone.add_init_script('localStorage.setItem("hifuu-player:HUMAN",'+json.dumps(p['token'])+')')
                    phone.goto(base+'/join?room=HUMAN');phone.locator('.station-axis').wait_for()
                    timer=phone.locator('.destination-actions [data-clock=decision]')
                    box=timer.bounding_box()
                    assert box and box['y']>=0 and box['y']+box['height']<=height,(width,box)
                    before=timer.inner_text()
                    phone.evaluate('window.savedAxis=document.querySelector(".station-axis")')
                    phone.wait_for_timeout(1400)
                    assert timer.inner_text()!=before
                    assert phone.evaluate('window.savedAxis===document.querySelector(".station-axis")')
                    phone.screenshot(path=str(out/f'countdown-{width}.png'))
                    # Lock submission while a different player owns the current decision window.
                    with server.store.lock:
                        room['phase']='decision';room['decision']['initial']=False
                        room['decision']['required']=[room['players'][1]['id']]
                    phone.locator('.decision-notice').wait_for()
                    target=phone.locator('.axis-stop:not(:disabled)').first
                    destination=target.get_attribute('data-destination')
                    target.click();phone.locator('[data-action=travel]').click()
                    phone.locator('.submitted-ticket').wait_for()
                    assert '等待 2号' in phone.locator('.submitted-ticket').inner_text()
                    assert phone.locator('[data-action=travel]').count()==0
                    assert room['decision']['choices'][str(p['id'])]['card']['nodes'][-1]==destination
                    phone.screenshot(path=str(out/f'submitted-{width}.png'))
                    # Unsubmitted stationary targets survive actual game ticks.
                    with server.store.lock:
                        room.update(phase='running',decision=None,updated=now_ms(),railAccum=0)
                    phone.locator('.station-axis').wait_for()
                    phone.evaluate('window.savedAxis=document.querySelector(".station-axis")')
                    tick=room['tick'];phone.wait_for_timeout(4300)
                    assert room['tick']>tick
                    assert phone.evaluate('window.savedAxis===document.querySelector(".station-axis")')
                    # Moving progress and countdown change in place on the same interval.
                    with server.store.lock:
                        offer=game.line_offer(EDGE_LINE['e131'],'s023','express')
                        card=game.select_destination(offer,'s023','s060')
                        room['railAccum']=0
                        p['queued']=card;game.depart(room,p)
                    phone.locator('.moving-screen').wait_for()
                    phone.evaluate('window.savedMoving=document.querySelector(".moving-screen")')
                    progress=phone.locator('.position-status').inner_text()
                    phone.wait_for_timeout(3000)
                    assert phone.evaluate('window.savedMoving===document.querySelector(".moving-screen")')
                    assert phone.locator('.position-status').inner_text()!=progress
                    phone.screenshot(path=str(out/f'moving-{width}.png'))
                    assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    phone.close();results.append(dict(width=width,countdown_visible=True,submitted_locked=True,stable_station_dom=True,stable_moving_dom=True))
                # Expiry is prominent immediately, rather than below the ticket axis.
                with server.store.lock:
                    room.update(phase='briefing',updated=now_ms())
                    p.update(station='s023',trip=None,queued=None)
                    game.open_decision(room,room['players'],initial=True)
                    room['decision']['remaining']=0
                phone=browser.new_page(viewport=dict(width=320,height=568),is_mobile=True,has_touch=True)
                phone.add_init_script('localStorage.setItem("hifuu-player:HUMAN",'+json.dumps(p['token'])+')')
                phone.goto(base+'/join?room=HUMAN');phone.locator('.submitted-ticket').wait_for()
                assert '已自动留站' in phone.locator('.submitted-ticket').inner_text()
                assert phone.locator('.submitted-ticket h1').bounding_box()['y']<300
                phone.screenshot(path=str(out/'timeout-320.png'))
                join=browser.new_page(locale='en-US');join.goto(base+'/join')
                join.locator('[name=room]').fill('AB');join.locator('[name=name]').fill('test')
                join.locator('[type=submit]').click();join.locator('#toast.show').wait_for()
                assert '房间码为5位' in join.locator('#toast').inner_text()
                assert not errors,errors
                report=dict(status='passed',phones=results,timeout_prominent=True,chinese_validation=True,errors=errors)
                (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report));browser.close()
        finally:
            stop.set();heartbeat.join();server.shutdown();server.server_close();thread.join()
if __name__=='__main__':main()
