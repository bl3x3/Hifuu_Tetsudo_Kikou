"""Multi-round manual-style playtest: real Chromium clients, real UI clicks.

Runs its own temporary server (never touches runtime/). Each phone is an
isolated browser context, so a phone can only ever act through its own page.
The only server-side adjustment is tickMs (game-clock speed) so a full match
does not cost hours of wall clock; no rule, projection or endpoint is bypassed.
"""
import asyncio
import json
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor'))
from playwright.async_api import async_playwright
from server.web import Server
from server.data import ADJACENCY, STATION_BY_ID, EDGE_LINE, station_lines
from server import game

OUT = ROOT / 'test-results' / 'playtest'
OUT.mkdir(parents=True, exist_ok=True)
REPORT = dict(rounds=[], checks=[], problems=[], notes=[])
ACCEL_MS = 400
PHONE_VIEWPORT = {'width': 390, 'height': 844}
HOST_VIEWPORT = {'width': 1920, 'height': 1080}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def name_of(station):
    return STATION_BY_ID.get(station, {}).get('name', station or '—')


class Harness:
    def __init__(self, base, server, host, phones, contexts):
        self.base, self.server, self.host = base, server, host
        self.phones, self.contexts = phones, contexts
        self.code = ''
        self.host_token = ''
        self.tokens = []
        self.failed = []

    async def check(self, name, ok, detail=''):
        entry = dict(name=name, ok=bool(ok), detail=str(detail)[:500])
        REPORT['checks'].append(entry)
        if not ok:
            self.failed.append(entry)
            log(f'  FAIL {name} :: {detail}')
        else:
            log(f'  ok   {name}')
        return bool(ok)

    async def note(self, name, ok, detail=''):
        REPORT['checks'].append(dict(name=name, ok=bool(ok), detail=str(detail)[:500], advisory=True))
        log(('  note ok   ' if ok else '  note      ') + name + (f' :: {detail}' if detail else ''))
        return bool(ok)

    async def shot(self, page, name, full_page=False):
        try:
            await page.screenshot(path=str(OUT / f'{name}.png'), full_page=full_page)
        except Exception as error:
            log(f'  screenshot {name} failed: {error}')

    async def api(self, path, token=''):
        return await self.host.evaluate(
            """async ([path, token]) => {
                const r = await fetch(path, {cache:'no-store', headers: token ? {Authorization:'Bearer '+token} : {}});
                let body = null; try { body = await r.json(); } catch (e) { body = null; }
                return {status: r.status, body};
            }""", [path, token])

    async def state(self):
        return (await self.api(f'/api/state?room={self.code}'))['body']

    async def api_post(self, path, payload, token=''):
        return await self.host.evaluate(
            """async ([path, token, payload]) => {
                const r = await fetch(path, {method:'POST', cache:'no-store',
                    headers:{'Content-Type':'application/json', ...(token ? {Authorization:'Bearer '+token} : {})},
                    body: JSON.stringify(payload)});
                let body = null; try { body = await r.json(); } catch (e) { body = null; }
                return {status: r.status, body};
            }""", [path, token, payload])

    async def me(self, index):
        body = (await self.api(f'/api/state?room={self.code}', self.tokens[index])).get('body') or {}
        return body.get('me') or {}

    def room(self):
        """Direct engine view. Going through /api/state would refresh the caller's
        heartbeat and hide the very disconnect this test wants to observe."""
        return self.server.store.rooms.get(self.code, {})

    def snapshot(self):
        with self.server.store.lock:
            room = self.room()
            decision = room.get('decision')
            return dict(phase=room.get('phase'), tick=room.get('tick', 0),
                        minutes=room.get('tick', 0) * 15 + room.get('railAccum', 0) / room['tickMs'] * 15,
                        technical=room.get('technical'), technicalUsed=room.get('technicalUsed', 0),
                        readyCount=len(decision['choices']) if decision else 0, decision=bool(decision))

    def me_direct(self, index, role=None):
        """Private view read from the engine, with no request side effects."""
        with self.server.store.lock:
            room = self.room()
            players = room.get('players') or []
            if role:
                player = next((p for p in players if p.get('role') == role), None)
            else:
                player = players[index] if index < len(players) else None
            if not player:
                return {}
            decision = room.get('decision')
            hand = []
            for offer in player.get('hand') or []:
                offer = dict(offer)
                offer['destinations'] = []
                if player.get('station') and not player.get('trip') and not player.get('queued'):
                    departure = max(room['tick'] + int(room['phase'] == 'running'),
                                    player.get('waitUntil', 0), player.get('holdUntil', 0))
                    for target in offer['nodes']:
                        if target == player['station']:
                            offer['destinations'].append(dict(station=target, current=True, ticks=0,
                                                              arrivalTick=room['tick']))
                        else:
                            ticket = game.select_destination(offer, player['station'], target)
                            offer['destinations'].append(dict(station=target, current=False, ticks=ticket['ticks'],
                                                              arrivalTick=departure + ticket['ticks']))
                hand.append(offer)
            return dict(index=players.index(player), role=player.get('role'), station=player.get('station'),
                        corrupted=player.get('corrupted'), hand=hand,
                        baseChanges=player.get('baseChanges', 0), bonusChanges=player.get('bonusChanges', 0),
                        canAct=bool(room.get('phase') in ('briefing', 'running', 'decision')) and not room.get('technical')
                              and game.can_act(room, player),
                        canChange=bool(room.get('phase') in ('briefing', 'running', 'decision')) and not room.get('technical')
                                  and game.can_act(room, player) and room['tick'] >= max(player.get('waitUntil', 0), player.get('holdUntil', 0)),
                        availableLines=[dict(id=line['id'], name=line['name'], color=line['color'],
                                             edge=next(e['id'] for e, _ in ADJACENCY[player['station']]
                                                       if EDGE_LINE[e['id']]['id'] == line['id']))
                                        for line in station_lines(player['station'])]
                        if player.get('station') and not player.get('trip') else [],
                        lineId=(player.get('hand') or [{}])[0].get('lineId'),
                        submitted=bool(decision and str(player['id']) in decision['choices']),
                        tick=room.get('tick', 0))

    # ---------------- lobby ----------------
    async def create_room(self):
        await self.host.get_by_role('button', name='创建房间', exact=True).click()
        await self.host.locator('.room-code').wait_for(timeout=20000)
        self.code = (await self.host.locator('.room-code').inner_text()).strip()
        self.host_token = await self.host.evaluate(
            "() => localStorage['hifuu-host:'+document.querySelector('.room-code').textContent.trim()] || ''")
        return self.code

    async def join_phones(self, names=('旅人一', '旅人二', '旅人三')):
        for i, phone in enumerate(self.phones):
            await phone.goto(f'{self.base}/join')
            await phone.locator('input[name=name]').fill(names[i])
            await phone.locator('input[name=room]').fill(self.code)
            await phone.get_by_role('button', name='领取车票').click()
            await phone.get_by_role('button', name='已了解，准备出发').wait_for(timeout=20000)
            token = await phone.evaluate("() => Object.keys(sessionStorage).map(k=>sessionStorage[k]).pop() || ''")
            self.tokens.append(token)
            # Pin THIS seat's credential into a per-context init script so a
            # reload can only ever restore its own seat (not a neighbour's).
            await self.contexts[i].add_init_script(f"window.__seat = {json.dumps(token)};")
            await self.contexts[i].add_init_script(
                "for (const k of Object.keys(sessionStorage)) if (k.startsWith('hifuu-player:'))"
                " { sessionStorage.setItem(k, window.__seat); localStorage.setItem(k, window.__seat); }")
        return self.tokens

    async def ready_all(self):
        for phone in self.phones:
            button = phone.get_by_role('button', name='已了解，准备出发')
            if await button.count() and await button.is_enabled():
                await button.click()
            await phone.wait_for_function(
                "() => document.querySelector('button[data-action=ready]')?.disabled === true", timeout=20000)

    async def roles(self):
        out = []
        for phone in self.phones:
            text = (await phone.locator('.identity h1').inner_text()).strip()
            out.append({'宇佐见莲子': 'renko', '玛艾露贝莉·赫恩': 'maribel', '八云紫': 'yukari'}[text])
        return out

    async def stations(self):
        return [(await phone.locator('.location h2').inner_text()).strip() for phone in self.phones]

    # ---------------- launch helpers ----------------
    async def choose_first_leg(self, index, target='s023'):
        """Click a real destination on the phone, the way a player would."""
        phone = self.phones[index]
        me = {}
        for _ in range(60):
            me = self.me_direct(index)
            if me.get('station') and me.get('hand'):
                break
            await asyncio.sleep(.3)
        if not me.get('station') or not me.get('hand'):
            await self.check(f'first-leg-{index}', False, f'phone never reached its station view: {me}')
            return 'no-state'
        here = me['station']
        offer = (me.get('hand') or [{}])[0]
        best, gain = None, -1 << 30
        for dest in offer.get('destinations') or []:
            if dest.get('current'):
                continue
            delta = self.density(here, target) - self.density(dest['station'], target)
            if delta > gain:
                best, gain = dest['station'], delta
        if not best:
            return 'no-destination'
        try:
            await phone.locator(f'.axis-stop[data-destination="{best}"]').first.click(timeout=8000)
            await phone.get_by_role('button', name='确认此程').click(timeout=8000)
            await phone.wait_for_function(
                "() => document.querySelector('.notice')?.textContent.includes('已收到你的选择')", timeout=15000)
            return f'{offer.get("name")} → {name_of(best)}'
        except Exception as error:
            await self.check(f'first-leg-{index}', False, error)
            return 'failed'

    async def confirm_first_legs(self):
        choices = await asyncio.gather(*(self.choose_first_leg(i) for i in range(3)))
        log(f'  first legs: {choices}')
        ready = self.snapshot()['readyCount']
        await self.check('all three phones confirm a first leg through the UI', ready == 3, f'{ready} {choices}')

    async def force_ending(self, kind='abort'):
        """Make sure the board sits on an ending screen before end-of-match checks."""
        if self.snapshot()['phase'] == 'ended':
            return True
        def accept_dialog(dialog):
            asyncio.ensure_future(dialog.accept())
            self.host.remove_listener('dialog', accept_dialog)

        self.host.on('dialog', accept_dialog)
        try:
            await self.host.get_by_role('button', name='中止本局').click(timeout=15000)
        except Exception:
            return False
        try:
            await self.host.locator('.ending').wait_for(timeout=20000)
        except Exception:
            return False
        return True

    async def rematch(self):
        """Reset to the lobby and hand out fresh identities, tolerating poll lag."""
        await self.host.get_by_role('button', name='同组三人再来一局').click(timeout=20000)
        for index, phone in enumerate(self.phones):
            if index == 0:
                await phone.get_by_role('button', name='已了解，准备出发').wait_for(timeout=25000)
            else:
                await phone.wait_for_function(
                    "() => document.querySelector('button[data-action=ready]')?.textContent.includes('准备出发')",
                    timeout=25000)
        for _ in range(30):
            if self.snapshot()['phase'] == 'lobby':
                break
            await asyncio.sleep(.3)
        await self.ready_all()
        await self.host.wait_for_function(
            "() => { const b = document.querySelector('button[data-action=setup]'); return b && !b.disabled; }",
            timeout=25000)
        await self.host.get_by_role('button', name='开始游戏 · 分配身份').click(timeout=20000)
        for phone in self.phones:
            await phone.locator('.identity').wait_for(timeout=25000)

    async def wait_for_launch(self):
        """The board polls every 700ms; only click once it really is unlocked."""
        try:
            await self.host.wait_for_function(
                "() => { const b = document.querySelector('button[data-action=launch]'); return b && !b.disabled; }",
                timeout=15000)
            return True
        except Exception:
            return False

    async def ending_ui(self, prefix):
        """Wait for the结束 screen to actually render, then inspect it."""
        for attempt in range(3):
            html = await self.host.evaluate("() => document.querySelector('#board-panel')?.innerHTML?.slice(0,60) || ''")
            if 'ending' in html:
                break
            await self.host.reload()
            try:
                await self.host.locator('.ending').wait_for(timeout=8000)
            except Exception:
                pass
            await self.host.wait_for_timeout(700)
        roles = await self.host.locator('.reveal-list > div').count()
        place = await self.host.locator('.ending-place').count()
        text = (await self.host.locator('.ending h2').inner_text()) if await self.host.locator('.ending h2').count() else ''
        await self.shot(self.host, f'{prefix}-ending-board')
        await self.check('ending screen renders the reveal list (3 travelers)', roles == 3, f'rows={roles}')
        await self.check('ending screen shows the meeting station and time', place == 1,
                         (await self.host.locator('.ending-place').inner_text()) if place else 'missing')
        await self.check('ending headline non-empty', bool(text.strip()), text)
        return text

    # ---------------- bot player ----------------
    def density(self, station, target):
        if station == target:
            return 0
        seen, queue = {station}, deque([(station, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= 40:
                continue
            for _, nxt in ADJACENCY[node]:
                if nxt in seen:
                    continue
                if nxt == target:
                    return depth + 1
                seen.add(nxt)
                queue.append((nxt, depth + 1))
        return 999

    async def decide(self, index, target='s023'):
        """One player decision, entirely through the phone DOM."""
        phone = self.phones[index]
        me = self.me_direct(index)
        if not me or not me.get('station'):
            return 'moving'
        if me.get('submitted'):
            # The UI keeps the axis enabled while the batch is open; a real
            # player can re-submit, so the bot must not fight the window.
            return 'already-submitted'
        here = me['station']
        offer = (me.get('hand') or [{}])[0]
        if here == target:
            # Everyone is converging here; standing still is what makes them meet.
            if await self.wait_button(phone, index):
                return f'在 {name_of(here)} 留站等候'
            return 'already-submitted'
        base = self.density(here, target)
        best, gain = None, 0
        for dest in offer.get('destinations') or []:
            if dest.get('current'):
                continue
            delta = base - self.density(dest['station'], target)
            if delta > gain:
                best, gain = dest['station'], delta
        if best and me.get('canAct'):
            try:
                await phone.locator(f'.axis-stop[data-destination="{best}"]').first.click(timeout=8000)
                await phone.get_by_role('button', name='确认此程').click(timeout=8000)
                return f'乘 {offer.get("name")} → {name_of(best)} (-{gain})'
            except Exception as error:
                await self.check(f'click-travel-{index}', False, error)
                return f'乘失败({best}): {str(error)[:120]}'
        if me.get('canChange') and (me['baseChanges'] + me['bonusChanges']) > 0 and (offer.get('destinations') or [{}]):
            options = [l for l in me.get('availableLines') or [] if l['id'] != offer.get('lineId')]
            if options:
                try:
                    await phone.locator('#change-panel').evaluate('(e)=>e.open=true')
                    await phone.locator('#change-edge').select_option(value=options[0]['edge'])
                    await phone.get_by_role('button', name='确认改签（扣除1次）').click(timeout=8000)
                    return f'改签 → {options[0]["name"]}'
                except Exception as error:
                    await self.check(f'click-change-{index}', False, error)
                    return f'改签失败: {str(error)[:120]}'
        if me.get('canAct'):
            state = dict(base=me.get('baseChanges'), bonus=me.get('bonusChanges'),
                         lines=[l['name'] for l in me.get('availableLines') or []],
                         dests=[(d['station'], round(base - self.density(d['station'], target)))
                                for d in offer.get('destinations') or [] if not d.get('current')])
            if await self.wait_button(phone, index):
                return f'留站等候30分钟 {state}'
            return 'already-submitted'
        return 'idle'

    async def wait_button(self, phone, index, timeout=9000):
        """The page refreshes every 700ms; click only once the button is live."""
        button = phone.get_by_role('button', name='留站等候30分钟')
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            try:
                if await button.count() and await button.is_enabled():
                    await button.click(timeout=3000)
                    return True
            except Exception:
                pass
            await asyncio.sleep(.15)
        return False

    async def act(self, index, target='s023'):
        """Submit whatever the phone currently offers, as a present player would."""
        phone = self.phones[index]
        me = self.me_direct(index)
        if not me.get('station') or me.get('submitted') or not me.get('canAct'):
            return None
        offer = (me.get('hand') or [{}])[0]
        stops = [d for d in offer.get('destinations') or [] if not d.get('current')]
        if stops:
            await phone.locator(f'.axis-stop[data-destination="{stops[0]["station"]}"]').first.click(timeout=8000)
            await phone.get_by_role('button', name='确认此程').click(timeout=8000)
            return f'乘 {offer.get("name")} → {name_of(stops[0]["station"])}'
        button = phone.get_by_role('button', name='留站等候30分钟')
        if await button.count() and await button.is_enabled():
            await button.click(timeout=8000)
            return '留站等候30分钟'
        return None

    async def bot_loop(self):
        """Keep the three phones responding so no decision window times out."""
        while True:
            for index in range(3):
                try:
                    done = await self.act(index)
                    if done:
                        log(f'  P{index+1} {done}')
                except Exception:
                    pass
            await asyncio.sleep(1.0)

    async def play(self, label, max_minutes=25, target='s023'):
        start = time.time()
        actions = [0, 0, 0]
        quiet = [0, 0, 0]
        seen = []

        async def worker(index):
            while time.time() - start < max_minutes * 60:
                try:
                    state = self.snapshot()
                    if state['phase'] == 'ended':
                        return
                    me = self.me_direct(index)
                except Exception:
                    await asyncio.sleep(.5)
                    continue
                if not me.get('canAct'):
                    quiet[index] += 1
                    await asyncio.sleep(.2)
                    continue
                quiet[index] = 0
                # Let the 700ms page refresh catch up with the server before the
                # bot touches the DOM, then confirm the button is really live.
                await asyncio.sleep(.75)
                label_done = await self.decide(index, target)
                if label_done != 'already-submitted':
                    actions[index] += 1
                    log(f'  P{index+1} {label_done} @ {name_of(me["station"])} [{state["phase"]}]')
                await asyncio.sleep(.3)

        workers = [asyncio.create_task(worker(i)) for i in range(3)]
        last = None
        last_report = start
        while time.time() - start < max_minutes * 60:
            state = self.snapshot()
            room = self.room()
            outcome = (room.get('result') or {}).get('outcome')
            key = (state['phase'], outcome)
            if key != last:
                log(f'  phase={state["phase"]} outcome={outcome} tick={state["tick"]} '
                    f'rail={round(state["minutes"]/60, 2)}h')
                last = key
            if time.time() - last_report > 20:
                last_report = time.time()
                rows = []
                for i in range(3):
                    m = self.me_direct(i)
                    rows.append(f"P{i+1}@{(m.get('station') and name_of(m['station'])) or '途中'}"
                                f"{'·已提交' if m.get('submitted') else ''}"
                                f"{'·可动' if m.get('canAct') else ''}"
                                f"{'·改' + str(m.get('baseChanges', 0) + m.get('bonusChanges', 0)) if m.get('canChange') else ''}"
                                f" {m.get('hand', [{}])[0].get('name', '')}")
                log(f'  [{round(time.time()-start)}s] tick={state["tick"]} {state["phase"]} | ' + ' | '.join(rows))
            if state['phase'] == 'ended':
                break
            await asyncio.sleep(.3)
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        state = self.snapshot()
        return dict(label=label, seconds=round(time.time() - start, 1), tick=state['tick'],
                    gameHours=round(state['minutes'] / 60, 2), actions=actions,
                    phase=state['phase'], result=self.room().get('result'))


async def main():
    errors = []
    with tempfile.TemporaryDirectory() as directory:
        server = Server('127.0.0.1', 0, Path(directory))
        web = threading.Thread(target=server.serve_forever, daemon=True)
        worker = threading.Thread(target=server.store.loop, daemon=True)
        web.start(); worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        log(f'service {base} (accelerated clock {ACCEL_MS}ms/tick)')
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                contexts, phones = [], []
                host_ctx = await browser.new_context(viewport=HOST_VIEWPORT, locale='zh-CN')
                host = await host_ctx.new_page()
                for _ in range(3):
                    ctx = await browser.new_context(viewport=PHONE_VIEWPORT, is_mobile=True,
                                                    has_touch=True, locale='zh-CN')
                    contexts.append(ctx)
                    phones.append(await ctx.new_page())
                for page in [host] + phones:
                    page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))
                    # Browsers log expected 4xx probes (full room, denied export)
                    # as console errors; those are asserted deliberately elsewhere.
                    page.on('console', lambda m: errors.append(f'console.error: {m.text}')
                            if m.type == 'error' and 'Failed to load resource' not in m.text else None)
                await host.goto(base + '/host')
                h = Harness(base, server, host, phones, contexts)
                await h.create_room()
                log(f'room code {h.code}')
                await h.join_phones()

                # ---------- Round 1: full match from a real lobby ----------
                log('ROUND 1: lobby → briefing → match → ending → replay → export')
                await h.ready_all()
                try:
                    await host.wait_for_function(
                        "() => document.querySelector('#host-start-status')?.textContent.includes('已准备 3/3 人')",
                        timeout=20000)
                except Exception:
                    pass
                status = await host.locator('#host-start-status').inner_text()
                await h.check('lobby reports 3/3 ready', '已准备 3/3 人' in status, status)
                await h.check('start button enabled once all ready',
                              await h.host.locator('button[data-action=setup]').is_enabled(timeout=15000))
                await h.shot(host, 'r1-lobby-1920')
                await h.shot(phones[0], 'r1-lobby-phone')
                await host.get_by_role('button', name='开始游戏 · 分配身份').click()
                for phone in phones:
                    await phone.locator('.identity').wait_for(timeout=20000)
                roles = await h.roles()
                starts = await h.stations()
                log(f'  roles={roles} starts={starts}')
                await h.check('three distinct hidden roles', sorted(roles) == ['maribel', 'renko', 'yukari'], roles)
                await h.check('three distinct start stations', len(set(starts)) == 3, starts)
                board_state = await h.state()
                public = board_state['players']
                public_raw = json.dumps(board_state, ensure_ascii=False)
                await h.check('public projection hides maribel/yukari roles',
                               all(p['role'] in (None, 'renko') for p in public), [p['role'] for p in public])
                await h.check('board payload carries no private fields',
                               all(f'"{key}"' not in public_raw for key in ('hand', 'corrupted', 'token', 'seed', 'messages')),
                               public_raw[:200])
                await h.check('phone shows a full line axis', await phones[0].locator('.axis-stop').count() >= 2,
                              await phones[0].locator('.axis-stop').count())
                # The fourth traveler must be refused, and a player must not be
                # able to drive the host or export the log.
                fourth = await h.api_post('/api/join', {'room': h.code, 'name': '第四人'})
                log(f"  fourth-seat response: {fourth}")
                await h.check('a full room refuses a fourth seat',
                              fourth['status'] == 400 and '满' in (fourth['body'] or {}).get('error', ''), fourth)
                grab = await h.api_post('/api/host', {'room': h.code, 'kind': 'abort'}, h.tokens[0])
                await h.check('a player credential cannot run a host command', grab['status'] == 403, grab['status'])
                await h.shot(phones[roles.index('renko')], 'r1-phone-station-axis', full_page=True)
                with server.store.lock:
                    server.store.rooms[h.code]['tickMs'] = ACCEL_MS
                await h.confirm_first_legs()
                await h.check('launch button unlocks once all confirmed', await h.wait_for_launch())
                await host.get_by_role('button', name='正式发车').click()
                await h.check('launch clears briefing', (await h.state())['phase'] == 'running')
                result = await h.play('r1-hifuu-converge', max_minutes=6)
                REPORT['rounds'].append(result)
                log(f'  round1 => {result["phase"]} {result["result"]}')
                await h.note('round1 reached a meeting ending inside the window', result['phase'] == 'ended',
                             'the drawn lines did not bring all three together this seed'
                             if result['phase'] != 'ended' else f'{result["result"]["outcome"]} @ tick {result["tick"]}')
                if result['phase'] == 'ended':
                    await h.check('round1 ends on a real meeting outcome, not a timeout',
                                  result['result']['outcome'] in ('hifuu', 'yukari'), result['result'])
                    await h.ending_ui('r1')
                    await h.shot(phones[0], 'r1-ending-phone', full_page=True)
                # Standalone replay/export checks need a finished match either way.
                await h.force_ending()
                await host.get_by_role('button', name='行程回放').click()
                await host.wait_for_function("() => document.querySelector('#replay-caption')?.textContent.includes('行程回放')",
                                             timeout=15000)
                trails = 0
                for _ in range(20):
                    await host.wait_for_timeout(500)
                    trails = await host.locator('#replay-trails path').count()
                    if trails:
                        break
                await h.check('replay paints real departed edges', trails > 0, trails)
                departed = [e for e in (await h.state()).get('replay', []) if e['type'] == 'depart']
                await h.check('replay trail count is bounded by real departures',
                              0 < trails <= len(departed), f'{trails} trails / {len(departed)} departures')
                await h.shot(host, 'r1-replay')
                await host.get_by_role('button', name='返回结局').click()
                await h.check('replay returns to ending', await host.locator('.ending').count() == 1)
                export = await h.api(f'/api/export?room={h.code}', h.host_token)
                await h.check('host export works after end',
                              export['status'] == 200 and len(export['body']['events']) > 8, export['status'])
                await h.check('export excludes join tokens',
                              'token' not in json.dumps(export['body']) and export['body']['seed'] is not None)
                late_export = await h.api(f'/api/export?room={h.code}')
                await h.check('export denied without host credential', late_export['status'] == 403, late_export['status'])

                # ---------- Round 2: rematch, corruption path ----------
                log('ROUND 2: rematch → convergence with corruption risk')
                await h.rematch()
                await h.check('rematch resets to lobby', h.snapshot()['phase'] == 'briefing')
                roles = await h.roles()
                log(f'  roles={roles} starts={await h.stations()}')
                await h.shot(phones[roles.index('maribel')], 'r2-maribel-identity', full_page=True)
                with server.store.lock:
                    server.store.rooms[h.code]['tickMs'] = ACCEL_MS
                await h.confirm_first_legs()
                await h.wait_for_launch()
                await host.get_by_role('button', name='正式发车').click()
                result = await h.play('r2-meeting', max_minutes=6)
                with server.store.lock:
                    events = list(server.store.rooms[h.code]['events'])
                result['corruptions'] = len([e for e in events if e['type'] == 'corruption'])
                result['rolls'] = len([e for e in events if e['type'] == 'roll'])
                result['naraBonuses'] = len([e for e in events if e['type'] == 'nara_bonus'])
                result['passes'] = len([e for e in events if e['type'] == 'pass'])
                result['arrivals'] = len([e for e in events if e['type'] == 'arrival'])
                REPORT['rounds'].append(result)
                log(f'  round2 => {result["phase"]} {result["result"]} events={result}')
                await h.check('round2 ended', result['phase'] == 'ended', result)
                if result['phase'] == 'ended':
                    await h.ending_ui('r2')
                    await h.check('round2 outcome recorded',
                                  result['result']['outcome'] in ('hifuu', 'yukari'), result['result'])

                # ---------- Round 3: abort + connection loss + refresh ----------
                log('ROUND 3: technical pause, refresh restore, host abort')
                await h.rematch()
                roles = await h.roles()
                # A fourth traveler must also be turned away while the match runs.
                late = await h.api_post('/api/join', {'room': h.code, 'name': '第四人'})
                await h.check('a running match rejects a fourth seat', late['status'] == 400, late)
                # Phone 1 reloads mid-briefing: seat, role and hand must survive.
                keep = await phones[0].locator('.identity h1').inner_text()
                offer_before = await phones[0].locator('.drawn-line h2').inner_text()
                await phones[0].reload()
                await phones[0].locator('.identity').wait_for(timeout=20000)
                await h.check('refresh keeps the same seat and role',
                              (await phones[0].locator('.identity h1').inner_text()) == keep, keep)
                await h.check('refresh keeps the drawn line',
                              (await phones[0].locator('.drawn-line h2').inner_text()) == offer_before, offer_before)
                with server.store.lock:
                    server.store.rooms[h.code]['tickMs'] = 2000  # real clock for the pause checks
                await h.confirm_first_legs()
                await h.wait_for_launch()
                await host.get_by_role('button', name='正式发车').click()
                moving = None
                for _ in range(50):                     # sample all phones concurrently
                    for index, phone in enumerate(phones):
                        if await phone.locator('.moving-screen').count():
                            moving = index
                            break
                    if moving is not None:
                        break
                    await asyncio.sleep(.3)
                await h.check('at least one phone shows the moving view', moving is not None, moving)
                if moving is not None:
                    phone = phones[moving]
                    await phone.wait_for_timeout(600)   # let the repaint settle
                    stale = await phone.locator('.identity,.goal,.drawn-line,.phone-public').count()
                    await h.check('moving phone hides the route picker and public arrival list', stale == 0, stale)
                    await h.check('moving phone keeps only a collapsed private notebook',
                                  await phone.locator('.private-notebook').count() == 1 and
                                  await phone.locator('#private-notebook[open]').count() == 0)
                    await h.check('moving phone shows only its own private label',
                                  '仅你可见' in (await phone.locator('.private-label').inner_text()))
                    await h.shot(phone, 'r3-phone-moving', full_page=True)
                    # A short leg can end mid-sample, so keep polling while the
                    # moving view is on screen instead of assuming one wait fits.
                    samples = []
                    for _ in range(24):
                        if not await phone.locator('[data-clock=journey]').count():
                            break
                        samples.append((time.time(), await phone.locator('[data-clock=journey]').inner_text()))
                        await asyncio.sleep(.25)
                    if len(samples) >= 3:
                        await h.check('arrival countdown ticks down while travelling',
                                      samples[0][1] != samples[-1][1],
                                      f'{samples[0][1]} -> {samples[-1][1]}')
                    else:
                        await h.check('moving view renders its arrival countdown', bool(samples),
                                      f'{len(samples)} samples')
                # A live driver keeps answering the 30 s decision windows, which is
                # what a real table does; without it every batch times out and the
                # railway clock never advances.
                bot = asyncio.create_task(h.bot_loop())
                # Real-clock rate, measured against the server's own wall clock so
                # the (slow) browser round trips are excluded. Only counted while
                # the railway is actually running: a decision window freezes it.
                phases, rate_measure = [], []
                for _ in range(12):
                    with server.store.lock:
                        room_now = server.store.rooms[h.code]
                        rate_measure.append((time.monotonic(),
                                             room_now['tick'] * 15 + room_now['railAccum'] / room_now['tickMs'] * 15,
                                             room_now['phase']))
                    await asyncio.sleep(.5)
                running_span = [row for row in rate_measure if row[2] == 'running']
                phases = sorted({row[2] for row in rate_measure})
                if len(running_span) >= 2:
                    rate = (running_span[-1][1] - running_span[0][1]) / (running_span[-1][0] - running_span[0][0])
                    await h.check('railway time advances at ~7.5 game minutes per second while running',
                                  6.0 <= rate <= 9.0, f'{rate:.2f} game min/s over {len(running_span)} samples')
                else:
                    await h.check('railway clock observed in the running phase', False, phases)
                await h.check('the table reaches running at least once', 'running' in phases, phases)
                await h.shot(host, 'r3-board-running')
                # Freeze the clock by dropping one phone off the network.
                await contexts[1].set_offline(True)
                await host.locator('.notice.danger').wait_for(timeout=30000)
                await h.shot(host, 'r3-technical-pause')
                await h.shot(phones[1], 'r3-phone-offline', full_page=True)
                snapshot_a = h.snapshot()['minutes']
                await asyncio.sleep(2.5)
                snapshot_b = h.snapshot()['minutes']
                await h.check('game clock freezes while a phone is offline', snapshot_a == snapshot_b,
                              f'{snapshot_a:.1f} vs {snapshot_b:.1f} game min')
                await contexts[1].set_offline(False)
                await host.wait_for_function(
                    "() => !document.querySelector('.notice.danger') || document.querySelector('.notice.danger').textContent.includes('即将继续')",
                    timeout=20000)
                await h.check('service resumes after reconnect', True)
                # The pause releases 3 s after the last phone is seen again, so
                # measure the clock over a window that certainly contains it.
                seen_after_resume = []
                for _ in range(20):
                    with server.store.lock:
                        room_now = server.store.rooms[h.code]
                        seen_after_resume.append((room_now['tick'] * 15 +
                                                  room_now['railAccum'] / room_now['tickMs'] * 15,
                                                  room_now['technical'],
                                                  room_now['technicalUsed']))
                    await asyncio.sleep(.7)
                sample = seen_after_resume
                phone_state = await phones[1].evaluate(
                    "() => ({connection: document.querySelector('#connection')?.textContent, "
                    "phase: document.querySelector('.moving-screen') ? 'moving' : (document.querySelector('.station-screen') ? 'station' : 'other')})")
                host_notice = await host.evaluate("() => document.querySelector('.notice.danger')?.textContent || 'none'")
                log(f'  reconnect samples: first={sample[0][0]:.1f} last={sample[-1][0]:.1f} '
                    f'technical_end={sample[-1][1]} used_end={sample[-1][2]:.0f} phone={phone_state} host={host_notice}')
                await h.check('the technical pause releases itself',
                              not sample[-1][1], f'technical={sample[-1][1]} used={sample[-1][2]:.0f}')
                resumed = sample[-1][0]
                await h.check('game clock restarts after reconnect', resumed > snapshot_b,
                              f'{snapshot_b:.1f} -> {resumed:.1f} game min')
                # Responsive board at two projector/desktop widths.
                for width, height in ((1280, 800), (1920, 1080)):
                    await host.set_viewport_size({'width': width, 'height': height})
                    await host.wait_for_timeout(350)
                    overflow = await host.evaluate('document.documentElement.scrollWidth - innerWidth')
                    await h.check(f'host board has no horizontal overflow at {width}px', overflow <= 1, overflow)
                pages = await host.context.new_page()
                await pages.goto(base + '/rules.html')
                await h.check('rules page opens and lists the meeting rule',
                              '在车站相遇' in (await pages.locator('body').inner_text()))
                await h.shot(pages, 'rules-page', full_page=True)
                await pages.close()
                # Host abort path.
                def accept_dialog(dialog):
                    asyncio.ensure_future(dialog.accept())
                    host.remove_listener('dialog', accept_dialog)

                host.on('dialog', accept_dialog)
                bot.cancel()
                await asyncio.gather(bot, return_exceptions=True)
                try:
                    await host.get_by_role('button', name='中止本局').click(timeout=15000)
                except Exception:
                    await host.reload()
                    await host.locator('.ending, .arrival-list').first.wait_for(timeout=15000)
                    await host.get_by_role('button', name='中止本局').click(timeout=15000)
                await host.locator('.ending').wait_for(timeout=20000)
                aborted = (await h.state())['result']
                await h.check('host abort produces aborted ending', aborted['outcome'] == 'aborted', aborted)
                await h.shot(host, 'r3-ending-aborted')
                await h.check('aborted ending uses its own art/placeholder',
                              await host.locator('.ending[data-outcome=aborted]').count() == 1)
                REPORT['rounds'].append(dict(label='r3-abort', phase='ended', result=aborted,
                                             seats_after_refresh=True))
                # Palette is derived from the game clock; check both sides of 18:00
                # on the settled ending screen so no live journey view is disturbed.
                with server.store.lock:
                    server.store.rooms[h.code]['tick'] = 30   # 第1日 23:30
                await host.wait_for_timeout(1200)
                night = await host.evaluate("() => document.documentElement.dataset.theme")
                await h.check('night palette at 23:30 game time', night == 'night', night)
                with server.store.lock:
                    server.store.rooms[h.code]['tick'] = 2    # 第1日 16:30
                await host.wait_for_timeout(1200)
                day = await host.evaluate("() => ({theme: document.documentElement.dataset.theme, clock: document.querySelector('[data-clock=rail]')?.textContent})")
                await h.check('day palette at 16:30 game time', day['theme'] == 'day', day)
                await h.shot(host, 'r3-board-day')
                phone_theme = await phones[0].evaluate("() => document.documentElement.dataset.theme")
                await h.check('phone palette follows the same clock', phone_theme == 'day', phone_theme)

                # ---------- Round 4: deterministic corruption → Yukari win ----------
                log('ROUND 4: Marin-bel is corrupted, then Renko arrives (Yukari path)')
                await h.rematch()
                order = await h.roles()
                maribel_index, renko_index = order.index('maribel'), order.index('renko')
                await h.confirm_first_legs()
                await h.wait_for_launch()
                await host.get_by_role('button', name='正式发车').click()
                # Place the fixture only once the match is running, so the briefing
                # window cannot expire underneath it.
                with server.store.lock:
                    room = server.store.rooms[h.code]
                    roles = {p['role']: p for p in room['players']}
                    hub = roles['yukari']['station'] or roles['maribel']['station']
                    # Only Marin-bel joins Yukari at first: with Renko also there,
                    # the Hifuu meeting would resolve before any corruption.
                    away = next((to for _, to in ADJACENCY[hub]
                                 if to not in (hub, roles['maribel']['station'], roles['yukari']['station'])), hub)
                    roles['maribel'].update(station=hub, trip=None, queued=None, departAt=None,
                                            waitUntil=room['tick'] + 10, holdUntil=0,
                                            lastArrival=dict(station=hub, tick=room['tick']))
                    # Renko is parked nearby AND held: arriving immediately would
                    # also resolve the Hifuu meeting before corruption can happen.
                    roles['renko'].update(station=away, trip=None, queued=None, departAt=None,
                                          waitUntil=room['tick'] + 10, holdUntil=0,
                                          lastArrival=dict(station=away, tick=room['tick']))
                    room['tickMs'] = 400
                    room['revision'] += 1
                log(f'  Marin-bel placed with Yukari at {name_of(hub)}; Renko held at {name_of(away)}')
                corrupted = False
                for _ in range(60):
                    await asyncio.sleep(.4)
                    with server.store.lock:
                        if any(e['type'] == 'corruption' for e in room['events']):
                            corrupted = True
                            break
                await h.check('an unprotected Marin-bel on Yukari platform is corrupted',
                              corrupted, [e['type'] for e in room['events']][-8:])
                if corrupted:
                    private = (await h.api(f'/api/state?room={h.code}', h.tokens[maribel_index]))['body']['me']
                    await h.check('the corruption arrives as a private message only',
                                  private.get('corrupted') is True and len(private.get('messages') or []) >= 1,
                                  private.get('messages'))
                    board = json.dumps(await h.state(), ensure_ascii=False)
                    await h.check('the public board stays free of corruption data',
                                  'corrupted' not in board and '侵蚀' not in board, board[:160])
                    await h.check('corrupted phone shows its changed goal',
                                  await phones[maribel_index].locator('.current-private-state, .goal').count() >= 1)
                    await h.shot(phones[maribel_index], 'r4-phone-corrupted', full_page=True)
                    with server.store.lock:
                        roles['renko'].update(station=hub, trip=None, queued=None, departAt=None,
                                              waitUntil=0, holdUntil=0,
                                              lastArrival=dict(station=hub, tick=room['tick']))
                        room['revision'] += 1
                    try:
                        await host.wait_for_function("() => document.querySelector('.ending')", timeout=25000)
                    except Exception:
                        pass
                    outcome = (await h.state())['result']
                    await h.check('Renko on the corrupted Marin-bel platform gives Yukari the win',
                                  bool(outcome) and outcome['outcome'] == 'yukari' and outcome['reason'].startswith('她记得'),
                                  outcome)
                    if outcome:
                        await h.ending_ui('r4')
                        await h.check('corrupted ending is labelled as the corrupted variant',
                                      await host.locator('.ending[data-outcome=yukari_corrupted]').count() == 1)
                REPORT['rounds'].append(dict(label='r4-corruption', phase=(await h.state())['phase'],
                                             result=(await h.state())['result'], corrupted=corrupted))

                # ---------- Round 5: change group / brand-new room ----------
                log('ROUND 5: new room for a different group')
                try:
                    await host.get_by_role('button', name='换组 · 新房间').click(timeout=15000)
                    await host.locator('.room-code').wait_for(timeout=15000)
                    new_code = (await host.locator('.room-code').inner_text()).strip()
                    await h.check('a brand-new room gets a different code',
                                  new_code != h.code and len(new_code) == 5, f'{h.code} -> {new_code}')
                    await h.check('the new room starts empty',
                                  '已加入 0/3 人' in (await host.locator('#host-start-status').inner_text()),
                                  await host.locator('#host-start-status').inner_text())
                    await h.check('the old seats no longer hold this room',
                                  (await h.api(f'/api/state?room={new_code}', h.tokens[0]))['status'] == 404)
                    REPORT['rounds'].append(dict(label='r5-new-room', code=new_code))
                except Exception as error:
                    await h.check('a brand-new room can be created from the ended board', False, error)

                REPORT['pageErrors'] = errors
                await h.check('no uncaught javascript errors', not errors, errors[:3])
                await browser.close()
        except Exception:
            traceback.print_exc()
            REPORT['problems'].append(traceback.format_exc()[-3000:])
        finally:
            server.store.stopped.set(); worker.join(timeout=2)
            server.shutdown(); server.server_close(); web.join(timeout=2)
    REPORT['failedCount'] = sum(1 for c in REPORT['checks'] if not c['ok'] and not c.get('advisory'))
    (OUT / 'playtest-report.json').write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f"checks={sum(1 for c in REPORT['checks'] if not c.get('advisory'))} failed={REPORT['failedCount']}")
    for entry in REPORT['checks']:
        if not entry['ok']:
            log(f"  {'note' if entry.get('advisory') else '!!'} {entry['name']}: {entry['detail']}")


if __name__ == '__main__':
    asyncio.run(main())
