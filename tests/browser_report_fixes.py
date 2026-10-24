"""Regression coverage for the MCP live report, using real HTTP and pointer clicks."""
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
from server.web import Server, now_ms


def main():
    out=ROOT/'test-results'/'report-fixes';out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server=Server('127.0.0.1',0,Path(directory))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        room=game.create_room('FIXES',now_ms())
        for name in ('A','B','C'):game.join(room,name,now_ms())['ready']=True
        game.setup(room,'S04',now_ms())
        for player,role in zip(room['players'],('renko','maribel','yukari')):player['role']=role
        p=room['players'][0]
        p.update(station='s023',lastArrival=dict(station='s023',tick=0),role='renko')
        p['hand']=[game.line_offer(EDGE_LINE['e016'],'s023','fixture')]
        server.store.rooms['FIXES']=room
        errors=[];geometry=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                for width,height in ((320,568),(360,640),(390,844)):
                    page=browser.new_page(viewport=dict(width=width,height=height),is_mobile=True,has_touch=True)
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.add_init_script('localStorage.setItem("hifuu-player:FIXES",'+json.dumps(p['token'])+')')
                    page.goto(base+'/join?room=FIXES')
                    page.locator('.station-axis').wait_for()
                    hits=page.evaluate('''() => {
                        const axis=document.querySelector('.station-axis').getBoundingClientRect();
                        const ticket=document.querySelector('.destination-actions').getBoundingClientRect();
                        const stops=[...document.querySelectorAll('.axis-stop:not(:disabled)')].filter(e=>{
                            const r=e.getBoundingClientRect(), x=r.left+r.width/2,y=r.top+r.height/2;
                            return y>Math.max(0,axis.top)&&y<Math.min(axis.bottom,ticket.top,innerHeight)&&e.contains(document.elementFromPoint(x,y));
                        });
                        return {visible:stops.map(e=>e.dataset.destination),axis:[axis.top,axis.bottom],ticket:ticket.top,overflow:document.documentElement.scrollWidth>innerWidth};
                    }''')
                    assert len(hits['visible'])>=2,(width,hits)
                    assert not hits['overflow'],hits
                    # Pointer click at the actual initial visible row, not an auto-scrolled locator.
                    target=page.locator('[data-destination="'+hits['visible'][0]+'"]')
                    box=target.bounding_box();page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
                    assert target.get_attribute('aria-pressed')=='true'
                    page.screenshot(path=str(out/f'station-{width}x{height}.png'))
                    # Polling with unchanged state must preserve scroll and selection.
                    before=page.evaluate('scrollY');page.wait_for_timeout(900)
                    assert page.evaluate('scrollY')==before
                    assert target.get_attribute('aria-pressed')=='true'
                    # Server changes version after the phone rendered its offer.
                    with server.store.lock:p['actionVersion']+=1
                    with page.expect_response(lambda r:r.url.endswith('/api/player')) as response:
                        page.locator('[data-action=travel]').click()
                    assert response.value.status==409
                    page.wait_for_timeout(300)
                    assert not page.locator('#toast').inner_text()
                    assert p['lastSeq']==0 # stale request must not execute a trip
                    page.locator('[data-action=wait]').click()
                    page.wait_for_timeout(300)
                    assert room['decision']['choices'][str(p['id'])]['kind']=='wait'
                    geometry.append(dict(width=width,height=height,**hits))
                    page.close()
                    with server.store.lock:
                        room['decision']['choices'].clear();p['lastSeq']=0
                host=browser.new_page(viewport=dict(width=1920,height=1080))
                host.on('pageerror',lambda e:errors.append(str(e)))
                host.add_init_script('localStorage.setItem("hifuu-host:FIXES",'+json.dumps(room['hostToken'])+')')
                host.on('dialog',lambda dialog:dialog.accept())
                host.goto(base+'/host?room=FIXES')
                # The first leg never times out: the host decides to leave unconfirmed players waiting.
                host.locator('[data-action=launch]:disabled').wait_for()
                assert host.locator('[data-clock=decision]').count()==0
                assert '首程不限时' in host.locator('#host-start-status').inner_text()
                with server.store.lock:
                    for player in room['players']:player['seen']=now_ms()
                host.locator('[data-action=launch-force]:enabled').click()
                host.locator('[data-action=launch]').wait_for(state='detached')
                assert room['phase']=='running' and room['decision'] is None
                assert [player['waitUntil'] for player in room['players']]==[2,2,2]
                forced=[e for e in room['events'] if e['type']=='choice_submitted' and e['source']=='host']
                assert len(forced)==3,forced
                host.screenshot(path=str(out/'initial-forced-wait.png'))
                with server.store.lock:
                    game.end(room,'hifuu','两人的约定，在此刻重合。','s060')
                # Every outcome has a supplied CG, shown whole in a 16:9 frame.
                host.locator('.ending[data-outcome=hifuu] img.ending-cg').wait_for()
                frame=host.locator('.ending-art').bounding_box()
                assert abs(frame['width']/frame['height']-16/9)<.02,frame
                assert host.locator('.ending-cg').evaluate('(e)=>getComputedStyle(e).objectFit')=='contain'
                host.screenshot(path=str(out/'ending-hifuu.png'))
                with server.store.lock:room['result'].update(outcome='yukari',reason='下一站，在境界的另一侧。');room['revision']+=1
                host.locator('[data-outcome=yukari_direct]').wait_for()
                host.screenshot(path=str(out/'ending-yukari.png'))
                assert host.request.get(base+'/favicon.ico').status==200
                # Kick/rejoin through the real host and player APIs; seats stay put.
                host.locator('[data-action=rematch]').click();host.locator('.seat-list').wait_for()
                host.locator('[data-action=kick]').nth(1).click()
                host.wait_for_function('()=>document.querySelectorAll("[data-action=kick]").length===2')
                assert host.locator('.seat-list .seat-number').all_text_contents()==[str(n) for n in range(1,7)]
                assert host.locator('.seat-name').all_text_contents()[:3]==['A','等待旅人','C']
                joined=host.request.post(base+'/api/join',data=dict(room='FIXES',name='D')).json()
                host.wait_for_function('()=>document.querySelectorAll("[data-action=kick]").length===3')
                assert joined['id']==4
                assert host.locator('.seat-name').all_text_contents()[:3]==['A','D','C']
                # An actual error toast is cleared after expiry, including accessible text.
                page=browser.new_page();page.goto(base+'/join')
                page.locator('[name=room]').fill('ZZZZZ');page.locator('[name=name]').fill('test')
                page.locator('[type=submit]').click();page.locator('#toast.show').wait_for()
                page.wait_for_function('()=>document.querySelector("#toast").textContent===""',timeout=6000)
                assert not errors,errors
                report=dict(status='passed',phone_geometry=geometry,stale_action_refreshed=True,forced_first_leg_waits=True,
                    stable_seats=True,ending_cg_16x9=True,favicon=True,toast_cleared=True,
                    limits='Chromium mobile emulation; venue Wi-Fi, physical phones and projection still require on-site checks.')
                (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report));browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':main()


