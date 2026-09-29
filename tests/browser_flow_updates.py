"""Regression for automatic departure, station hints, pills and rejoin QR."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
from playwright.sync_api import sync_playwright, expect
from server import game
from server.data import EDGE_LINE, POSITIONS
from server.web import Server, now_ms


def main():
    out=ROOT/'test-results'/'flow-updates'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server=Server('127.0.0.1',0,Path(directory))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        room=game.create_room('FLOWS',now_ms())
        for name in ('旅人甲','旅人乙','旅人丙'):
            game.join(room,name,now_ms())['ready']=True
        game.setup(room,'S04',now_ms())
        p=room['players'][0]
        p.update(station='s023',lastArrival=dict(station='s023',tick=0))
        p['hand']=[game.line_offer(EDGE_LINE['e016'],'s023','flow')]
        server.store.rooms[room['code']]=room
        stop=threading.Event()
        def presence():
            while not stop.wait(.1):
                with server.store.lock:
                    room['hostSeen']=now_ms()
                    for player in room['players']:player['seen']=now_ms()
        heartbeat=threading.Thread(target=presence,daemon=True)
        heartbeat.start()
        errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                screen=browser.new_page(viewport=dict(width=1920,height=1080))
                screen.on('pageerror',lambda e:errors.append(str(e)))
                screen.goto(base+'/screen?room=FLOWS')
                host=browser.new_page(viewport=dict(width=1280,height=900))
                host.add_init_script('localStorage.setItem("hifuu-host:FLOWS",'+json.dumps(room['hostToken'])+')')
                host.goto(base+'/host?room=FLOWS')
                host.on('pageerror',lambda e:errors.append(str(e)))
                phones=[]
                for width,height in ((320,568),(390,844)):
                    phone=browser.new_page(viewport=dict(width=width,height=height),is_mobile=True,has_touch=True)
                    phone.on('pageerror',lambda e:errors.append(str(e)))
                    phone.add_init_script('localStorage.setItem("hifuu-player:FLOWS",'+json.dumps(p['token'])+')')
                    phone.goto(base+'/join?room=FLOWS')
                    phone.locator('.station-axis').wait_for()
                    assert phone.locator('[data-clock=decision]').count()==0
                    phone.locator('.station-axis').evaluate('(el)=>el.scrollTop=0')
                    hint=phone.locator('.axis-scroll-hint')
                    expect(hint).to_be_visible()
                    hint.click()
                    phone.wait_for_function('()=>document.querySelector(".station-axis").scrollTop>0')
                    phone.locator('.station-axis').evaluate('(el)=>el.scrollTop=el.scrollHeight')
                    expect(hint).to_be_hidden()
                    phone.locator('.station-axis').evaluate('(el)=>el.scrollTop=0')
                    expect(hint).to_be_visible()
                    assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    phone.screenshot(path=str(out/f'station-{width}.png'))
                    phone.locator('[data-action=open-change]').click()
                    expect(phone.locator('#change-line-detail')).to_contain_text('本站邻站')
                    expect(phone.locator('#change-edge option').first).to_contain_text('↔')
                    phone.screenshot(path=str(out/f'change-{width}.png'))
                    phones.append(phone)
                # Pills remain centred on the public station and never add leader lines.
                with server.store.lock:
                    for player in room['players'][:2]:player['lastArrival']=dict(station='s049',tick=0)
                screen.wait_for_function('()=>document.querySelectorAll(".station-player-pill[data-station=s049] .player-marker").length===2')
                pill=screen.locator('.station-player-pill[data-station=s049]')
                assert pill.locator('path').count()==0
                rect=pill.locator(':scope > rect')
                assert float(rect.get_attribute('x'))+float(rect.get_attribute('width'))/2==POSITIONS['s049'][0]
                assert float(rect.get_attribute('y'))+float(rect.get_attribute('height'))/2==POSITIONS['s049'][1]
                screen.screenshot(path=str(out/'map-pills.png'))
                # Pause can be armed before submission and retained after the final choice.
                with server.store.lock:
                    room['phase']='decision'
                    game.open_decision(room,[p])
                host.locator('[data-action=pause]').click()
                expect(host.locator('[data-action=pause]')).to_have_text('恢复自动继续')
                phones[0].locator('[data-action=wait]').click()
                expect(phones[0].locator('.submitted-ticket')).to_contain_text('主持人已暂停')
                host.wait_for_timeout(3300)
                assert room['phase']=='decision'
                host.screenshot(path=str(out/'host-paused.png'))
                host.locator('[data-action=pause]').click()
                expect(phones[0].locator('[data-clock=decision]').first).to_be_visible()
                screen.screenshot(path=str(out/'auto-continue.png'))
                host.locator('[data-action=pause]').wait_for(state='hidden',timeout=7000)
                assert room['phase']=='running'
                # Wait expiry becomes a new decision after 30 game minutes.
                with server.store.lock:
                    game.rail_step(room)
                    game.rail_step(room)
                expect(host.locator('[data-action=pause]')).to_be_visible()
                assert room['decision']['required']==[p['id']]
                with server.store.lock:
                    room['technical']=dict(resumeAt=None)
                    room['technicalUsed']=1000
                    # Keep the connection missing for the screenshot, despite fixture heartbeats.
                    for player in room['players']:player['seen']=now_ms()-6000
                stop.set()
                heartbeat.join()
                expect(screen.locator('.rejoin-card')).to_contain_text('扫码返回本局')
                qr=screen.locator('.rejoin-card img')
                expect(qr).to_be_visible()
                screen.wait_for_function('()=>document.querySelector(".rejoin-card img").naturalWidth>0')
                assert 'room=FLOWS' in qr.get_attribute('src')
                assert screen.request.get(base+qr.get_attribute('src')).status==200
                screen.screenshot(path=str(out/'disconnected-qr.png'))
                screen.set_viewport_size(dict(width=1366,height=768))
                box=qr.bounding_box()
                assert box and box['y']>=0 and box['y']+box['height']<=768,box
                screen.screenshot(path=str(out/'disconnected-qr-1366.png'))
                with server.store.lock:
                    game.end(room,'aborted','本次连接中断持续90秒未恢复，本局中止。')
                expect(screen.locator('.ending')).to_be_visible()
                expect(screen.locator('.rejoin-card')).to_have_count(0)
                assert not errors,errors
                browser.close()
                (out/'report.json').write_text(json.dumps(dict(passed=True,errors=errors,checks=[
                    '320/390px scroll hint and transfer details','no initial timeout',
                    'centred station pills','host pause and automatic resume',
                    'wait expiry reopens decision','disconnected spectator QR, hidden after abort']),ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            stop.set()
            heartbeat.join()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__=='__main__':main()
