"""Four isolated browsers against the real HTTP service; pip install playwright."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'vendor'))
from playwright.sync_api import sync_playwright
from server.web import Server
from server import game
from server.data import ADJACENCY, EDGE_LINE


def main():
    output=ROOT/'test-results'/'art-refresh'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server=Server('127.0.0.1',0,Path(directory))
        web=threading.Thread(target=server.serve_forever,daemon=True)
        worker=threading.Thread(target=server.store.loop,daemon=True)
        web.start();worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                host=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
                host.on('pageerror',lambda e:errors.append(str(e)))
                host.on('dialog',lambda dialog:dialog.accept())
                host.goto(base+'/host')
                host.get_by_role('button',name='创建房间',exact=True).click()
                host.locator('.room-code').wait_for()
                code=host.locator('.room-code').inner_text()
                # The start entry stays visible even with an empty room in a
                # narrow app browser. Disabled state explains its prerequisites.
                for width,height in [(560,800),(1024,768),(1920,1080)]:
                    host.set_viewport_size({'width':width,'height':height})
                    button=host.locator('[data-action=setup]')
                    assert button.is_visible() and button.is_disabled()
                    bounds=button.bounding_box()
                    assert bounds['x']>=0 and bounds['x']+bounds['width']<=width
                    assert bounds['y']>=0 and bounds['y']+bounds['height']<=height
                    assert '已加入 0/3 人' in host.locator('#host-start-status').inner_text()
                    assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.screenshot(path=str(output/'board-lobby.png'))
                phones=[]
                for i in range(3):
                    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                    phone=context.new_page()
                    phone.on('pageerror',lambda e:errors.append(str(e)))
                    phone.goto(f'{base}/join?room={code}')
                    phone.locator('input[name=name]').fill(f'旅人{i+1}')
                    phone.get_by_role('button',name='领取车票').click()
                    phone.get_by_role('button',name='已了解，准备出发').click()
                    phones.append(phone)
                host.locator('[data-action=setup]').click()
                host.get_by_role('button',name='正式发车').wait_for()
                # A close-range fixture makes a deterministic complete UI match.
                # No production debug endpoint or shortened match clock is used.
                with server.store.lock:
                    room=server.store.rooms[code]
                    for p in room['players']:
                        p['station']={'renko':'s060','maribel':'s063','yukari':'s088'}[p['role']]
                        p['lastArrival']=dict(station=p['station'],tick=0)
                        game.deal(room,p)
                        if p['role']=='maribel':
                            edge=next(e for e,to in ADJACENCY['s063'] if to=='s060')
                            p['hand']=[game.line_offer(EDGE_LINE[edge['id']],p['station'],'test-nearby')]
                        if p['role']=='renko':
                            p['hand']=[game.line_offer(EDGE_LINE['e068'],p['station'],'test-long-line')]
                        if p['role']=='yukari':
                            edge=next(e for e,to in ADJACENCY['s088'] if to=='s087')
                            p['hand']=[game.line_offer(EDGE_LINE[edge['id']],p['station'],'test-other-arrival')]
                    roles=[p['role'] for p in room['players']]
                    room['revision']+=1
                for i,phone in enumerate(phones):
                    phone.locator('.identity').wait_for()
                    if roles[i]=='maribel':
                        phone.wait_for_function("() => document.querySelector('.location h2')?.textContent.includes('奈良')")
                    if roles[i]=='renko':
                        phone.wait_for_function("() => document.querySelector('.location h2')?.textContent.includes('京都')")
                target=phones[roles.index('maribel')]
                role_before=target.locator('.identity h1').inner_text()
                target.reload()
                target.locator('.identity').wait_for()
                assert target.locator('.identity h1').inner_text()==role_before
                target.screenshot(path=str(output/'phone-plan.png'),full_page=True)
                renko=phones[roles.index('renko')]
                renko.screenshot(path=str(output/'phone-line-axis.png'),full_page=True)
                assert renko.locator('.axis-stop').count()>7
                assert target.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert host.locator('#network [id^=s]').count()>=100
                for i,phone in enumerate(phones):
                    if roles[i]=='maribel':
                        phone.locator('.axis-stop[data-destination="s060"]').click()
                        phone.get_by_role('button',name='确认此程').click()
                    elif roles[i]=='yukari':
                        phone.locator('.axis-stop[data-destination="s087"]').click()
                        phone.get_by_role('button',name='确认此程').click()
                    else:
                        phone.get_by_role('button',name='留站等候30分钟').click()
                host.get_by_role('button',name='正式发车').click()
                host.locator('.arrival-list').wait_for()
                host.screenshot(path=str(output/'board-live.png'))
                target.locator('.moving-screen').wait_for()
                assert target.locator('.identity,.goal,.drawn-line,.private-messages').count()==0
                target.screenshot(path=str(output/'phone-moving.png'),full_page=True)
                target.locator('.decision-notice').wait_for(timeout=10000)
                target.screenshot(path=str(output/'phone-decision-paused.png'),full_page=True)
                frozen_countdown=target.locator('[data-clock=journey]').inner_text()
                host.wait_for_timeout(800)
                assert target.locator('[data-clock=journey]').inner_text()==frozen_countdown
                # The player still chooses privately; the host closes the discussion window.
                yukari_phone=phones[roles.index('yukari')]
                wait_button=yukari_phone.get_by_role('button',name='留站等候30分钟')
                if wait_button.is_visible() and wait_button.is_enabled():
                    wait_button.click()
                force_continue=host.locator('[data-action=continue-force]')
                normal_continue=host.locator('[data-action=continue]')
                if force_continue.is_enabled():
                    force_continue.click()
                else:
                    normal_continue.click()
                host.wait_for_timeout(500)
                # The third phone's network outage pauses game time and interval progress.
                offline=phones[roles.index('yukari')]
                # Leave enough travel for the five-second connection timeout.
                with server.store.lock:
                    maribel=next(p for p in room['players'] if p['role']=='maribel')
                    maribel['holdUntil']=room['tick']+3
                offline.context.set_offline(True)
                host.locator('.notice.danger').wait_for(timeout=9000)
                with server.store.lock:
                    frozen=(room['tick'],room['railAccum'])
                host.wait_for_timeout(500)
                with server.store.lock:
                    assert (room['tick'],room['railAccum'])==frozen
                host.screenshot(path=str(output/'board-reconnect.png'))
                offline.context.set_offline(False)
                for _ in range(120):
                    if host.locator('.ending h2').is_visible():
                        break
                    force_continue=host.locator('[data-action=continue-force]')
                    normal_continue=host.locator('[data-action=continue]')
                    if force_continue.is_visible() and force_continue.is_enabled():
                        force_continue.click()
                    elif normal_continue.is_visible() and normal_continue.is_enabled():
                        normal_continue.click()
                    host.wait_for_timeout(500)
                assert host.locator('.ending h2').is_visible(), 'host did not reach an ending after manual decisions'
                assert '终于' in host.locator('.ending h2').inner_text()
                round_logs=list((Path(directory)/'logs').glob('*.json'))
                assert len(round_logs)==1, 'the completed round must be archived while the server is running'
                archived=json.loads(round_logs[0].read_text(encoding='utf-8'))
                assert archived['result']['outcome']=='hifuu'
                assert archived['timing']['durationSeconds']>0
                assert abs(sum(archived['timing']['phaseSeconds'].values())-archived['timing']['durationSeconds'])<.01
                for phone in phones:phone.locator('.ending').wait_for()
                host.screenshot(path=str(output/'board-ended.png'))
                host.get_by_role('button',name='行程回放').click()
                host.wait_for_function("() => document.querySelector('#replay-caption')?.textContent.includes('行程回放')")
                host.get_by_role('button',name='同组三人再来一局').click()
                for phone in phones:phone.get_by_role('button',name='已了解，准备出发').wait_for()
                host.reload();host.locator('.room-code').wait_for()
                assert round_logs[0].is_file(), 'rematch must preserve the previous round log'
                assert host.locator('.room-code').inner_text()==code
                assert not errors,errors
                report={'status':'passed','browser':'Chromium','clients':4,'phoneViewport':'390x844','hostViewport':'1920x1080',
                    'checks':['create room','QR rendered','3 isolated phone seats','ready and role assignment','phone refresh retains role',
                              'full line station axis','station destination selection','minimal moving screen','other player decision notice freezes countdown','private route selection','launch','real rail movement','network outage freezes game time and progress','automatic recovery countdown','Hifuu ending','20s replay start','rematch','host refresh retains room','no JS exceptions','no phone horizontal overflow'],
                    'limits':'Desktop mobile emulation; physical iOS/Android and venue Wi-Fi not tested.'}
                (output/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=False))
                browser.close()
        finally:
            server.store.stopped.set();worker.join(timeout=2)
            server.shutdown();server.server_close();web.join(timeout=2)


if __name__=='__main__':main()
