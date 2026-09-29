"""Check game-time presentation and both palettes against a temporary HTTP server."""
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
from playwright.sync_api import sync_playwright
from server import game
from server.data import EDGE_LINE, EDGE_BY_ID
from server.web import Server, now_ms


def main():
    output=ROOT/'test-results'/'art-refresh'; output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        server=Server('127.0.0.1',0,Path(directory))
        web=threading.Thread(target=server.serve_forever,daemon=True);web.start()
        base=f'http://127.0.0.1:{server.server_port}'
        room=game.create_room('THEME',now_ms())
        for name in ('旅人一','旅人二','旅人三'):
            game.join(room,name,now_ms())['ready']=True
        game.setup(room,'random',now_ms())
        room['decision']['remaining']=300000  # Stable screenshot fixture, no production override.
        for p,role,station in zip(room['players'],('renko','maribel','yukari'),('s060','s063','s088')):
            p.update(role=role,station=station,lastArrival=dict(station=station,tick=0))
            game.deal(room,p)
        renko=room['players'][0]
        renko['hand']=[game.line_offer(EDGE_LINE['e068'],'s060','theme-fixture')]
        server.store.rooms[room['code']]=room
        errors=[]
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                host=browser.new_page(viewport={'width':1920,'height':1080})
                host.add_init_script('localStorage.setItem("hifuu-host:THEME",'+json.dumps(room['hostToken'])+')')
                phone=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                phone.add_init_script('localStorage.setItem("hifuu-player:THEME",'+json.dumps(renko['token'])+')')
                screen=browser.new_page(viewport={'width':1920,'height':1080})
                for page,path in ((host,'host'),(phone,'join'),(screen,'screen')):
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'{base}/{path}?room=THEME')
                    page.locator('[data-clock=rail]').wait_for()
                colors={};checked=[]
                for tick,theme,label in ((0,'day','第1日 16:00'),(7,'day','第1日 17:45'),(8,'night','第1日 18:00'),
                                         (32,'night','第2日 00:00'),(55,'night','第2日 05:45'),(56,'day','第2日 06:00')):
                    with server.store.lock:
                        room.update(tick=tick,railAccum=0,updated=now_ms(),hostSeen=now_ms())
                        room['revision']+=1
                        for p in room['players']:p['seen']=now_ms()
                    for page in (host,phone,screen):
                        page.wait_for_function('(x)=>document.documentElement.dataset.theme===x.theme && document.querySelector("[data-clock=rail]")?.textContent===x.label',arg=dict(theme=theme,label=label))
                        assert page.locator('[data-clock=match],[data-clock=technical]').count()==0
                        assert not re.search(r'\d+\s*刻|真实剩余|七分钟|7\s*分钟|双时钟',page.locator('body').inner_text())
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    stroke=host.locator('#e068 path:last-child').evaluate('(e)=>getComputedStyle(e).stroke')
                    ink=phone.locator('.line-heading h2').evaluate('(e)=>getComputedStyle(e).color')
                    assert stroke==ink,(stroke,ink)
                    colors[theme]=stroke
                    checked.append(label)
                    if tick in (0,8):
                        # The atlas furniture must neither clip public records nor cover stations.
                        for board in (host,screen):
                            geometry=board.evaluate('''() => {
                                const panel=document.querySelector('.board-panel');
                                const furniture=[...document.querySelectorAll('.atlas-heading,.atlas-postcard,.atlas-legend,.board-panel')].map(e=>e.getBoundingClientRect());
                                const covered=[...document.querySelectorAll('#network g[id^="s"] .station-label')].filter(e=>{
                                    const b=e.getBoundingClientRect();
                                    return furniture.some(r=>b.left<r.right&&b.right>r.left&&b.top<r.bottom&&b.bottom>r.top);
                                }).map(e=>e.textContent);
                                return {covered,clipped:panel.scrollHeight>panel.clientHeight+1, heights:[panel.clientHeight,panel.scrollHeight,...[...panel.children].map(e=>[e.className,e.getBoundingClientRect().height,getComputedStyle(e).marginTop,getComputedStyle(e).marginBottom])],
                                    stations:document.querySelectorAll('#network g[id^="s"]').length,
                                    edges:document.querySelectorAll('#network g[data-category]').length};
                            }''')
                            assert geometry['covered']==[],geometry
                            assert not geometry['clipped'],geometry
                            assert geometry['stations']==100 and geometry['edges']==132,geometry
                        host.screenshot(path=str(output/f'board-{theme}.png'))
                        host.screenshot(path=str(output/f'board-paper-{theme}.png'),clip=dict(x=135,y=140,width=580,height=135))
                        phone.screenshot(path=str(output/f'phone-{theme}.png'),full_page=True)
                assert colors['day']!=colors['night']
                # A 60-game-minute express is displayed as 01:00, never 00:08 real time.
                with server.store.lock:
                    room.update(tick=8,updated=now_ms(),hostSeen=now_ms())
                    renko['station']='s023'
                    renko['queued']=game.make_card(['s023','s060'],[EDGE_BY_ID['e131']],'express-fixture')
                    game.depart(room,renko)
                phone.locator('.moving-screen').wait_for()
                assert phone.locator('[data-clock=journey]').inner_text()=='01:00'
                assert '小时:分钟' in phone.locator('.journey-countdown').inner_text()
                phone.screenshot(path=str(output/'phone-moving-night.png'),full_page=True)
                assert not errors,errors
                report=dict(status='passed',boundaries=checked,line_colors_match=True,
                            atlas_stations=100,atlas_edges=132,atlas_furniture_covers_no_station_labels=True,public_records_unclipped=True,
                            express_game_countdown='01:00',no_match_time_limit=True,decision_countdown_allowed=True,no_tick_units=True,
                            limits='Chromium desktop and mobile emulation; physical devices not tested.')
                (output/'theme-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(report,ensure_ascii=True))
                browser.close()
        finally:
            server.shutdown();server.server_close();web.join(timeout=2)


if __name__=='__main__':main()
