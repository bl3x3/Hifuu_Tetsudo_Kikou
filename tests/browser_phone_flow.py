"""Focused browser checks for the phone route-change flow and notebook UX."""
import asyncio
import tempfile
import threading
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor'))
from playwright.async_api import async_playwright
from server.web import Server


def log(msg):
    print(msg, flush=True)


async def main():
    results = []
    with tempfile.TemporaryDirectory() as directory:
        server = Server('127.0.0.1', 0, Path(directory))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=server.store.loop, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            host = await browser.new_page(viewport={'width': 1600, 'height': 900})
            await host.goto(base + '/host')
            await host.get_by_role('button', name='创建房间', exact=True).click()
            await host.locator('.room-code').wait_for()
            code = (await host.locator('.room-code').inner_text()).strip()
            phones = []
            for i in range(3):
                ctx = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
                page = await ctx.new_page()
                await page.goto(f'{base}/join')
                await page.locator('input[name=name]').fill(f'P{i+1}')
                await page.locator('input[name=room]').fill(code)
                await page.get_by_role('button', name='领取车票').click()
                await page.get_by_role('button', name='已了解，准备出发').click()
                await page.wait_for_function("() => document.querySelector('button[data-action=ready]')?.disabled === true")
                phones.append(page)
            await host.get_by_role('button', name='开始游戏 · 分配身份').click()
            for page in phones:
                await page.locator('.identity').wait_for()
            token = await phones[0].evaluate("() => Object.keys(sessionStorage).map(k=>sessionStorage[k])[0]")
            me = await host.evaluate(
                """async ([code, token]) => (await (await fetch('/api/state?room='+code, {headers:{Authorization:'Bearer '+token}})).json()).me""",
                [code, token])

            def check(name, ok, detail=''):
                results.append((bool(ok), name, str(detail)[:160]))
                log(('ok   ' if ok else 'FAIL ') + name + (' :: ' + str(detail)[:160] if detail else ''))

            # ---- destination pick updates the ticket, not the server ----
            phone = phones[0]
            stops = phone.locator('.axis-stop:not(.current)')
            check('the full line axis is offered', await stops.count() >= 1, await stops.count())
            target = await stops.last.get_attribute('data-destination')
            await stops.last.click()
            ticket = await phone.locator('.ticket-route').inner_text()
            check('tapping a stop fills the ticket without submitting',
                  '选择目的地' not in ticket, ticket.replace('\n', ' '))
            confirm = phone.get_by_role('button', name='确认此程')
            check('confirm unlocks only after a destination is chosen', await confirm.is_enabled())
            await confirm.click()
            await phone.wait_for_function("() => document.querySelector('.notice')?.textContent.includes('已收到你的选择')")
            check('submitting shows the received notice', True)
            # ---- a second tap must not break the window ----
            if await stops.count() > 1:
                await stops.first.click()
                await confirm.click()
                await phone.wait_for_timeout(800)
                toast = await phone.locator('#toast').inner_text()
                check('re-picking inside the same window is handled without a raw error',
                      '行动窗口' not in toast, toast or '(no toast)')
            # ---- change line before submitting ----
            # Phone 2 opens the change panel and swaps its drawn line.
            phone2 = phones[1]
            token2 = await phone2.evaluate("() => Object.keys(sessionStorage).map(k=>sessionStorage[k])[0]")
            before = await phone2.locator('.drawn-line h2').inner_text()
            options = await phone2.locator('#change-edge option').count()
            check('change panel lists the other lines at this station', options >= 1, options)
            if options:
                chosen = await phone2.locator('#change-edge option').first.get_attribute('value')
                await phone2.locator('#change-panel').evaluate('(e)=>e.open=true')
                await phone2.locator('#change-edge').select_option(value=chosen)
                await phone2.get_by_role('button', name='确认改签（扣除1次）').click()
                await phone2.wait_for_timeout(1500)
                after = await phone2.locator('.drawn-line h2').inner_text()
                tickets = await phone2.locator('.drawn-line .line-code').inner_text()
                me2 = await host.evaluate(
                    """async ([code, token]) => (await (await fetch('/api/state?room='+code, {headers:{Authorization:'Bearer '+token}})).json()).me""",
                    [code, token2])
                check('changing the line swaps the drawn service', after != before, f'{before} -> {after}')
                check('the change spends exactly one change budget',
                      me2['baseChanges'] + me2['bonusChanges'] == 0,
                      me2['baseChanges'] + me2['bonusChanges'])
                check('the new line offers its own station axis',
                      await phone2.locator('.axis-stop').count() >= 2, await phone2.locator('.axis-stop').count())
                check('the axis colour follows the drawn line',
                      after and (await phone2.locator('.drawn-line').get_attribute('style') or '').startswith('--line-color'), '')
                check('the station code label stays in sync', bool(tickets.strip()), tickets)
            # ---- wait state is visible and counted down ----
            phone3 = phones[2]
            await phone3.get_by_role('button', name='留站等候30分钟').click()
            await phone3.wait_for_timeout(1500)
            check('waiting at a station is announced on the phone',
                  '留站等候' in (await phone3.locator('.location').inner_text()), '')
            check('the wait button reports itself as received',
                  await phone3.locator('.notice').count() == 1)
            # ---- public arrival record is available but collapsed ----
            public = phone3.locator('#phone-public')
            check('public arrival record exists and starts collapsed',
                  await public.count() == 1 and not await public.evaluate('(e)=>e.open'))
            rows = await public.locator('p').count()
            check('public arrival record lists all three seats', rows == 3, rows)
            host_text = await host.locator('.arrival-list').inner_text()
            check('the board shows the same three seat numbers',
                  all(str(n) in host_text for n in (1, 2, 3)), host_text.replace('\n', ' ')[:80])
            await phone3.screenshot(path=str(ROOT / 'test-results' / 'playtest' / 'phone-waiting.png'), full_page=True)
            await phones[1].screenshot(path=str(ROOT / 'test-results' / 'playtest' / 'phone-after-change.png'), full_page=True)
            await browser.close()
        server.store.stopped.set()
        server.shutdown()
        server.server_close()
    failed = [r for r in results if not r[0]]
    log(f'checks={len(results)} failed={len(failed)}')
    for ok, name, detail in failed:
        log(f'  !! {name}: {detail}')


asyncio.run(main())
