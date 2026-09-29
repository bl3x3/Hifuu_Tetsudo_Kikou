"""Small LAN server: server-authoritative state, private projections and snapshots."""
import argparse
import copy
import io
import json
import mimetypes
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, unquote
from .data import ROOT, STATIONS, EDGES, TEMPLATES, POSITIONS, LINES, live_map
from . import game, journal
from .dialogue import public_map_locations

try:
    import qrcode
    import qrcode.image.svg
except ImportError:
    qrcode = None

def now_ms():
    return time.monotonic_ns() // 1_000_000


class SaveError(Exception):
    pass


class Store:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.rooms = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.archive_errors = {}
        for path in directory.glob('room-*.json'):
            try:
                room = json.loads(path.read_text(encoding='utf-8'))
                room.pop('matchRemaining', None)  # Retire the previous real-time limit in old snapshots.
                room.setdefault('requests', [])
                for i, player in enumerate(room['players']):
                    player.setdefault('seat', i+1)
                    player.setdefault('requestUsed', False)
                if room['phase'] not in ('lobby', 'ended'):
                    game.end(room, 'aborted', '主机服务已重启，本局安全中止。请由主持人重新开局。')
                    if room.get('journal'):
                        room['journal'].update(timeQuality='interrupted', endedAt=None,
                                               restartDetectedAt=journal.wall_now())
                        room['events'][-1].update(at=None, elapsedMs=None,
                            observedElapsedMs=room['journal']['elapsedMs'],
                            detectedAt=room['journal']['restartDetectedAt'])
                room['updated'], room['hostSeen'] = now_ms(), 0
                for p in room['players']:
                    p['seen'] = 0
                self.rooms[room['code']] = room
                if room['phase'] == 'ended':
                    self.save(room)
            except SaveError as error:
                print(f'恢复后的房间等待重新保存：{error}')
            except (OSError, ValueError, KeyError):
                print(f'忽略无法读取的房间存档：{path.name}')

    def write_json(self, target, value):
        tmp = target.with_suffix('.tmp')
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open('w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        except OSError as error:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SaveError('无法写入存档，请检查主机磁盘空间与目录权限后重试。') from error

    def archive(self, room):
        if room['phase'] != 'ended':
            return
        clock = room.get('journal') or {}
        stamp = (clock.get('startedAt') or clock.get('createdAt') or 'unknown-time')[:19].replace(':','-')
        code = ''.join(c for c in room['code'] if c.isalnum())
        target = self.directory / 'logs' / f"{stamp}_{code}_{journal.round_id(room)}.json"
        if not target.exists():
            self.write_json(target, journal.record(room))
        self.archive_errors.pop(journal.round_id(room), None)
        return target

    def save(self, room):
        self.write_json(self.directory / f"room-{room['code']}.json", room)
        try:
            self.archive(room)
        except SaveError as error:
            # The snapshot is already durable. Do not undo a completed match;
            # retry the archive each second and require it before replacing this round.
            key = journal.round_id(room)
            if key not in self.archive_errors:
                print(f"本局已保存快照，独立日志待重试（{room['code']}）：{error}")
            self.archive_errors[key] = str(error)

    def loop(self):
        last_save = 0
        while not self.stopped.wait(.1):
            with self.lock:
                now = now_ms()
                for room in self.rooms.values():
                    phase = room['phase']
                    game.advance(room, now)
                    if (phase != 'ended' and room['phase'] == 'ended') or now - last_save >= 1000:
                        try:
                            self.save(room)
                        except SaveError as error:
                            if room['phase'] in ('briefing', 'running', 'decision'):
                                game.end(room, 'aborted', '无法保存房间状态，本局已中止。请检查主机磁盘。')
                            print(f'保存失败：{error}')
                if now - last_save >= 1000:
                    last_save = now


def addresses(port):
    ips = {'127.0.0.1'}
    preferred = None
    try:
        # UDP connect only asks the OS for its default route; no packet is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))
            preferred = probe.getsockname()[0]
            ips.add(preferred)
    except OSError:
        pass
    try:
        ips.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return [f'http://{ip}:{port}' for ip in sorted(ips, key=lambda ip: (ip.startswith('127.'), ip != preferred, ip)) if not ip.startswith('169.254.')]


def public_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError('公网地址必须是 http://域名:端口 或 https://域名。')
    try:
        parsed.port
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return f'{parsed.scheme}://{parsed.netloc}'


def configured_public_url(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8')).get('publicUrl')
    return public_url(value) if value else None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, host, port, directory, public_address=None):
        super().__init__((host, port), Handler)
        self.store = Store(directory)
        self.addresses = addresses(self.server_port)
        self.public_address = public_address
        if public_address:
            self.addresses = [public_address] + [address for address in self.addresses if address != public_address]
        self.map_svg = live_map().encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else '') not in ('200', '304'):
            super().log_message(fmt, *args)

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, post):
        try:
            # Consume a bounded payload before an early rejection. Closing a
            # Windows socket with unread request bytes can reset the connection
            # before the client receives our 403/415 response.
            raw = None
            if post:
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    raise ValueError('请求大小不合法。') from None
                if not 0 < length <= 16384:
                    self.send(413, {'error': '请求大小不合法。'})
                    return
                raw = self.rfile.read(length)
            allowed_hosts = {urlsplit(a).netloc for a in self.server.addresses}
            allowed_hosts.add(f'localhost:{self.server.server_port}')
            if self.headers.get('Host') not in allowed_hosts:
                self.send(403, {'error': '请通过主机列出的地址访问。'})
                return
            parsed = urlsplit(self.path)
            query = {k: values[0] for k, values in parse_qs(parsed.query).items()}
            if post:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.send(415, {'error': '请求必须使用 JSON。'})
                    return
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    self.send(403, {'error': '拒绝跨站操作。'})
                    return
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError('无效请求。')
            else:
                body = {}
            if parsed.path.startswith('/api/'):
                with self.server.store.lock:
                    room = self.server.store.rooms.get(str(body.get('room') or query.get('room') or '').upper())
                    before = copy.deepcopy(room) if post and room else None
                    try:
                        self.api(parsed.path, query, body, post)
                    except SaveError:
                        if before is not None:
                            room.clear()
                            room.update(before)
                        raise
            elif not post:
                self.static(parsed.path)
            else:
                self.send(404, {'error': '没有此页面。'})
        except game.StaleAction as error:
            self.send(409, {'error': str(error), 'code': 'stale_action'})
        except SaveError as error:
            self.send(503, {'error': str(error)})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send(400, {'error': '请求格式不正确。'})
        except (KeyError, TypeError):
            self.send(400, {'error': '请求参数不正确。'})
        except ValueError as error:
            self.send(400, {'error': str(error) or '请求无效。'})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def api(self, path, query, body, post):
        store, now = self.server.store, now_ms()
        routes = {'/api/rooms': True, '/api/join': True, '/api/player': True, '/api/host': True,
                  '/api/config': False, '/api/state': False, '/api/qr': False, '/api/export': False}
        if path not in routes:
            self.send(404, {'error': '没有此接口。'})
            return
        if routes[path] != post:
            self.send(405, {'error': '请求方式不正确。'})
            return
        if path == '/api/config' and not post:
            self.send(200, dict(stations=STATIONS, edges=EDGES, positions=POSITIONS, templates=TEMPLATES, lines=LINES,
                                dialogueMap=dict(startTick=game.DIALOGUE_START_TICK, locations=public_map_locations()),
                                addresses=self.server.addresses, publicAddress=self.server.public_address,
                                qrAvailable=qrcode is not None, version='0.1.0'))
            return
        if path == '/api/rooms' and post:
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                self.send(403, {'error': '请在主机电脑通过 localhost 打开主持端。'})
                return
            previous = None
            if body.get('previousRoom'):
                previous = store.rooms.get(str(body['previousRoom']).upper())
                credential = self.headers.get('Authorization', '').removeprefix('Bearer ')
                if not previous or not secrets.compare_digest(credential, previous['hostToken']):
                    self.send(403, {'error': '只有原房间主持人可以切换观众屏。'})
                    return
                if previous['phase'] != 'ended':
                    raise ValueError('请先结束当前房间，再开启新房间。')
                store.archive(previous)
                if any(candidate.get('previousRoom') == previous['code'] for candidate in store.rooms.values()):
                    raise ValueError('此房间已经开启后续房间，请刷新主持端。')
            active = [r for r in store.rooms.values() if r['phase'] != 'ended']
            if len(active) >= 12:
                raise ValueError('活动房间过多，请先结束已有房间。')
            while True:
                code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
                if code not in store.rooms:
                    break
            room = game.create_room(code, now)
            if previous:
                room['previousRoom'] = previous['code']
            store.save(room)
            store.rooms[code] = room
            self.send(200, dict(code=code, token=room['hostToken']))
            return
        room = store.rooms.get(str(body.get('room') or query.get('room') or '').upper())
        if not room:
            self.send(404, {'error': '找不到房间，请核对房间码。'})
            return
        credential = self.headers.get('Authorization', '').removeprefix('Bearer ')
        host = secrets.compare_digest(credential, room['hostToken'])
        player = next((p for p in room['players'] if secrets.compare_digest(credential, p['token'])), None)
        phase = room['phase']
        game.advance(room, now)
        if phase != 'ended' and room['phase'] == 'ended':
            store.save(room)
        if path == '/api/state' and not post:
            if credential and not host and player is None:
                self.send(401, {'error': '座位凭证已失效，请重新加入。'})
                return
            if host:
                room['hostSeen'] = now
            if player:
                player['seen'] = now
            state = game.private_state(room, player, now) if player else game.public_state(room, now)
            state['nextRoom'] = next((candidate['code'] for candidate in store.rooms.values()
                                      if candidate.get('previousRoom') == room['code']), None)
            self.send(200, state)
            return
        if path == '/api/join' and post:
            player = game.join(room, body.get('name'), now)
            joined_address = next((address for address in self.server.addresses
                                   if urlsplit(address).netloc == self.headers.get('Host')), None)
            if self.client_address[0] not in ('127.0.0.1', '::1') and joined_address in self.server.addresses:
                room['joinAddress'] = joined_address
            store.save(room)
            self.send(200, dict(token=player['token'], id=player['id'], code=room['code']))
            return
        if path == '/api/qr' and not post:
            if qrcode is None:
                raise ValueError('二维码组件未安装，请使用网址和房间码加入。')
            address = query.get('address')
            if address not in self.server.addresses:
                raise ValueError('请选择已配置的加入地址。')
            output = io.BytesIO()
            qrcode.make(f"{address}/join?room={room['code']}", image_factory=qrcode.image.svg.SvgPathImage, border=3).save(output)
            self.send(200, output.getvalue(), 'image/svg+xml')
            return
        if path == '/api/player' and post and player:
            player['seen'] = now
            if body.get('kind') == 'ready':
                if room['phase'] != 'lobby':
                    raise ValueError('房间已经开始。')
                player['ready'] = bool(body.get('ready', True))
                room['revision'] += 1
            else:
                game.action(room, player, body)
            store.save(room)
            self.send(200, game.private_state(room, player, now))
            return
        if path == '/api/host' and post and host:
            room['hostSeen'] = now
            kind = body.get('kind')
            if kind == 'setup':
                game.setup(room, body.get('template', 'random'), now)
            elif kind == 'launch':
                game.launch(room, now, bool(body.get('force')))
            elif kind == 'continue':
                game.continue_decision(room, bool(body.get('force')))
            elif kind == 'pause':
                game.pause_decision(room, body.get('paused'))
            elif kind == 'dialogue':
                game.set_dialogue_enabled(room, body.get('enabled'))
            elif kind == 'abort':
                game.end(room, 'aborted', '主持人中止了本局。')
            elif kind == 'kick':
                if room['phase'] != 'lobby':
                    raise ValueError('只能在开局前移除席位。')
                room['players'] = [p for p in room['players'] if p['id'] != body.get('player')]
                room['revision'] += 1
            elif kind == 'rematch':
                if room['phase'] != 'ended':
                    raise ValueError('请先完成或中止当前对局。')
                store.archive(room)
                fresh = game.create_room(room['code'], now)
                fresh['previousRoundId'] = journal.round_id(room)
                fresh['hostToken'] = room['hostToken']
                fresh['joinAddress'] = room.get('joinAddress')
                fresh['dialogueEnabled'] = room.get('dialogueEnabled', False)
                fresh['players'] = [dict(id=p['id'], seat=p['seat'], token=p['token'], name=p['name'], seen=p['seen'],
                    ready=False, messages=[], lastSeq=p['lastSeq']) for p in room['players']]
                room.clear()
                room.update(fresh)
            else:
                raise ValueError('未知主持操作。')
            store.save(room)
            self.send(200, game.public_state(room, now))
            return
        if path == '/api/export' and not post and host:
            if room['phase'] != 'ended':
                raise ValueError('对局结束后才能导出真实行程。')
            self.send(200, journal.record(room))
            return
        self.send(403, {'error': '无权执行此操作。'})

    def static(self, path):
        if path == '/favicon.ico':
            path = '/favicon.svg'
        if path == '/map.svg':
            self.send(200, self.server.map_svg, 'image/svg+xml')
            return
        if path in ('/', '/host', '/join', '/screen'):
            path = '/index.html'
        target = (ROOT / 'public' / unquote(path).lstrip('/')).resolve()
        if not target.is_relative_to((ROOT / 'public').resolve()) or not target.is_file():
            self.send(404, {'error': '没有此文件。'})
            return
        self.send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'application/octet-stream')


def main():
    parser = argparse.ArgumentParser(description='秘封铁道纪行 · Python 局域网服务')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--public-url', type=public_url, default=configured_public_url(ROOT / 'server-config.json'),
                        help='公网玩家访问地址，覆盖 server-config.json 中的 publicUrl，优先用于加入链接和二维码')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'runtime')
    args = parser.parse_args()
    server = Server(args.bind, args.port, args.data_dir, args.public_url)
    worker = threading.Thread(target=server.store.loop, daemon=True)
    worker.start()
    print(f'秘封铁道纪行 v0.1.0\n主持端：http://localhost:{server.server_port}/host', flush=True)
    for address in server.addresses:
        print(f'手机加入：{address}/join', flush=True)
    if qrcode is None:
        print('二维码未启用；运行 python -m pip install -r requirements.txt 后重启。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务已停止。')
    finally:
        server.store.stopped.set()
        worker.join(timeout=2)
        with server.store.lock:
            for room in server.store.rooms.values():
                try:
                    server.store.save(room)
                except SaveError as error:
                    print(error)
        server.server_close()
