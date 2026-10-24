import json
import copy
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from server import game
from server.web import Server, Store, public_url, configured_public_url


class PublicConfigTest(unittest.TestCase):
    def test_invalid_deployment_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'server-config.json'
            for value in ([], {'publicUrl': 123}, {'publicUrl': ''}, {'publicURL': None}):
                with self.subTest(value=value):
                    config_path.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        configured_public_url(config_path)

    def test_persisted_public_url_and_lan_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'server-config.json'
            self.assertIsNone(configured_public_url(config_path))
            config_path.write_text(
                json.dumps({'publicUrl': 'https://game.example.com:8000/'}), encoding='utf-8'
            )
            self.assertEqual(configured_public_url(config_path), 'https://game.example.com:8000')
            config_path.write_text(json.dumps({'publicUrl': None}), encoding='utf-8')
            self.assertIsNone(configured_public_url(config_path))


class WebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = Server(
            '127.0.0.1', 0, Path(self.temp.name), public_url('https://game.example.com:8000/')
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, token='', extra=None):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if extra:
            headers.update(extra)
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, (
                    json.loads(raw)
                    if 'application/json' in response.headers['Content-Type']
                    else raw
                )
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def room(self):
        return self.request('/api/rooms', {})[1]

    def test_restored_lobbies_free_capacity_without_losing_rooms_or_credentials(self):
        store = self.server.store
        snapshots = {}
        credentials = []
        for i in range(12):
            room = game.create_room(f'R{i:04d}', 10000)
            player = game.join(room, f'旅人{i}', 10000)
            store.rooms[room['code']] = room
            store.save(room)
            snapshots[room['code']] = (
                Path(self.temp.name) / f"room-{room['code']}.json"
            ).read_bytes()
            credentials.append((room['code'], room['hostToken'], player['token'], player['id']))

        # Zero is the restored "not seen" marker even early in the new process's uptime.
        with patch('server.web.now_ms', return_value=60000):
            self.server.store = Store(Path(self.temp.name))
            for room in self.server.store.rooms.values():
                self.assertEqual(room['hostSeen'], 0)
                self.assertTrue(all(player['seen'] == 0 for player in room['players']))
            status, created = self.request('/api/rooms', {})
            self.assertEqual(status, 200)
            self.assertEqual(len(self.server.store.rooms), 13)
            self.assertNotIn(created['code'], snapshots)
            for code, snapshot in snapshots.items():
                path = Path(self.temp.name) / f'room-{code}.json'
                self.assertTrue(path.read_bytes() == snapshot, 'existing snapshot was changed')
                status, state = self.request(f'/api/state?room={code}')
                self.assertEqual(status, 200)
                self.assertEqual(state['phase'], 'lobby')

            code, host_token, player_token, player_id = credentials[0]
            status, state = self.request(
                '/api/host', dict(room=code, kind='dialogue', enabled=True), host_token
            )
            self.assertEqual(status, 200)
            self.assertTrue(state['dialogueEnabled'])
            status, state = self.request(
                '/api/player', dict(room=code, kind='ready'), player_token
            )
            self.assertEqual(status, 200)
            self.assertEqual(state['me']['id'], player_id)
            ready_player = next(player for player in state['players'] if player['id'] == player_id)
            self.assertTrue(ready_player['ready'])

    def test_idle_lobbies_free_capacity_even_when_background_advance_updates_them(self):
        now = 2000000
        idle = now - 30 * 60 * 1000 - 1
        for i in range(12):
            room = game.create_room(f'I{i:04d}', idle)
            game.join(room, f'旅人{i}', idle)
            game.advance(room, now)
            self.server.store.rooms[room['code']] = room
            self.assertEqual(room['updated'], now)

        with patch('server.web.now_ms', return_value=now):
            status, _ = self.request('/api/rooms', {})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.server.store.rooms), 13)
        self.assertTrue(all(room['phase'] == 'lobby' for room in self.server.store.rooms.values()))

    def test_twelve_recent_lobbies_still_reject_new_rooms(self):
        now = 2000000
        for i in range(12):
            room = game.create_room(f'A{i:04d}', now - 30 * 60 * 1000 + 1)
            self.server.store.rooms[room['code']] = room

        with patch('server.web.now_ms', return_value=now):
            status, body = self.request('/api/rooms', {})
        self.assertEqual(status, 400)
        self.assertIn('活动房间过多', body['error'])
        self.assertEqual(len(self.server.store.rooms), 12)

    def test_player_heartbeat_keeps_lobby_active_when_host_is_idle(self):
        now = 2000000
        for i in range(11):
            room = game.create_room(f'A{i:04d}', now - 1000)
            self.server.store.rooms[room['code']] = room
        room = game.create_room('GUEST', now - 31 * 60 * 1000)
        game.join(room, '仍在等候的旅人', now - 1000)
        self.server.store.rooms[room['code']] = room

        with patch('server.web.now_ms', return_value=now):
            status, body = self.request('/api/rooms', {})
        self.assertEqual(status, 400)
        self.assertIn('活动房间过多', body['error'])
        self.assertEqual(len(self.server.store.rooms), 12)

    def test_in_progress_games_keep_capacity_even_with_old_heartbeats(self):
        now = 2000000
        idle = now - 31 * 60 * 1000
        for phase in ('briefing', 'running'):
            with self.subTest(phase=phase):
                self.server.store.rooms.clear()
                for i in range(12):
                    room = game.create_room(f'P{i:04d}', now)
                    for name in ('旅人甲', '旅人乙', '旅人丙'):
                        game.join(room, name, now)['ready'] = True
                    game.setup(room, 'S05', now)
                    if phase == 'running':
                        game.launch(room, now, True)
                    room['hostSeen'] = idle
                    for player in room['players']:
                        player['seen'] = idle
                    self.server.store.rooms[room['code']] = room

                with patch('server.web.now_ms', return_value=now):
                    status, body = self.request('/api/rooms', {})
                self.assertEqual(status, 400)
                self.assertIn('活动房间过多', body['error'])
                self.assertEqual(len(self.server.store.rooms), 12)
                self.assertTrue(
                    all(room['phase'] == phase for room in self.server.store.rooms.values())
                )

    def test_server_configuration_is_not_served_as_static_content(self):
        for path in (
            '/config/dialogues.json',
            '/config/messages.json',
            '/config/rules.json',
            '/server-config.json',
            '/data/edges.csv',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        status, text = self.request('/game-text.json')
        self.assertEqual(status, 200)
        self.assertIn('renko', text['roles'])

    def test_dialogue_map_config_contains_only_public_locations(self):
        from server import game
        from server.dialogue import QUOTES

        status, config = self.request('/api/config')
        self.assertEqual(status, 200)
        settings = config['dialogueMap']
        self.assertEqual(settings['startTick'], game.DIALOGUE_START_TICK)
        locations = settings['locations']
        expected = {station for quote in QUOTES.values() for station in quote['stations']}
        self.assertEqual({item['station'] for item in locations}, expected)
        self.assertEqual(len(locations), len(expected))
        self.assertEqual([item['station'] for item in locations if item['expressOnly']], ['s023'])
        for item in locations:
            self.assertEqual(set(item), {'station', 'expressOnly'})
            self.assertIn(item['station'], config['positions'])

    def test_dialogue_host_permission_persistence_and_rematch(self):
        host = self.room()
        code = host['code']
        player = self.request('/api/join', dict(room=code, name='旅人'))[1]
        command = dict(room=code, kind='dialogue', enabled=True)
        self.assertEqual(self.request('/api/host', command)[0], 403)
        self.assertEqual(self.request('/api/host', command, player['token'])[0], 403)
        self.assertEqual(
            self.request('/api/host', {**command, 'enabled': 'true'}, host['token'])[0], 400
        )
        with patch('server.web.os.replace', side_effect=OSError('disk full')):
            self.assertEqual(self.request('/api/host', command, host['token'])[0], 503)
        self.assertFalse(self.request(f'/api/state?room={code}')[1]['dialogueEnabled'])
        status, state = self.request('/api/host', command, host['token'])
        self.assertEqual(status, 200)
        self.assertTrue(state['dialogueEnabled'])
        restored = Store(Path(self.temp.name)).rooms[code]
        self.assertTrue(restored['dialogueEnabled'])
        self.assertEqual(restored['dialogues'], [])
        self.request('/api/host', dict(room=code, kind='abort'), host['token'])
        self.assertEqual(self.request('/api/host', command, host['token'])[0], 400)
        status, state = self.request('/api/host', dict(room=code, kind='rematch'), host['token'])
        self.assertEqual(status, 200)
        self.assertTrue(state['dialogueEnabled'])
        self.assertEqual(state['publicDialogues'], [])
        self.assertEqual(self.server.store.rooms[code]['dialogueProgress'], {})

    def test_public_domain_config_join_and_qr(self):
        public_address = 'https://game.example.com:8000'
        headers = {'Host': 'game.example.com:8000', 'Origin': public_address}
        status, config = self.request('/api/config', extra=headers)
        self.assertEqual(status, 200)
        self.assertEqual(config['publicAddress'], public_address)
        self.assertEqual(config['addresses'][0], public_address)
        self.assertEqual(self.request('/', extra=headers)[0], 200)
        self.assertEqual(self.request('/join', extra=headers)[0], 200)
        host = self.room()
        status, player = self.request(
            '/api/join', dict(room=host['code'], name='公网旅人'), extra=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.request(
                '/api/player', dict(room=host['code'], kind='ready'), player['token'], headers
            )[0],
            200,
        )
        with patch('server.web.qrcode') as qr_module:
            self.assertEqual(
                self.request(
                    f"/api/qr?room={host['code']}&address={public_address}", extra=headers
                )[0],
                200,
            )
            self.assertEqual(
                qr_module.make.call_args.args[0], f"{public_address}/join?room={host['code']}"
            )
        self.assertEqual(
            self.request(
                '/api/join',
                dict(room=host['code'], name='跨站'),
                extra={**headers, 'Origin': 'http://evil.example'},
            )[0],
            403,
        )

    def test_spectator_follows_authorized_new_room_and_persists(self):
        host = self.room()
        code = host['code']
        self.assertEqual(self.request('/api/rooms', dict(previousRoom=code), host['token'])[0], 400)
        self.request('/api/host', dict(room=code, kind='abort'), host['token'])
        self.assertEqual(self.request('/api/rooms', dict(previousRoom=code))[0], 403)
        with patch('server.web.os.replace', side_effect=OSError('disk full')):
            self.assertEqual(
                self.request('/api/rooms', dict(previousRoom=code), host['token'])[0], 503
            )
        self.assertIsNone(self.request(f'/api/state?room={code}')[1]['nextRoom'])
        status, successor = self.request('/api/rooms', dict(previousRoom=code), host['token'])
        self.assertEqual(status, 200)
        state = self.request(f'/api/state?room={code}')[1]
        self.assertEqual(state['nextRoom'], successor['code'])
        self.assertNotIn(successor['token'], json.dumps(state))
        self.assertEqual(self.request('/api/rooms', dict(previousRoom=code), host['token'])[0], 400)
        self.assertIsNone(self.request(f"/api/state?room={successor['code']}")[1]['nextRoom'])
        self.server.store = Store(Path(self.temp.name))
        self.assertEqual(self.request(f'/api/state?room={code}')[1]['nextRoom'], successor['code'])
        self.request('/api/host', dict(room=successor['code'], kind='abort'), successor['token'])
        status, third = self.request(
            '/api/rooms', dict(previousRoom=successor['code']), successor['token']
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.request(f"/api/state?room={successor['code']}")[1]['nextRoom'], third['code']
        )

    def test_save_failures_roll_back_creation_join_player_and_host(self):
        with patch('server.web.os.replace', side_effect=PermissionError('private path')):
            status, body = self.request('/api/rooms', {})
        self.assertEqual(status, 503)
        self.assertIn('无法写入存档', body['error'])
        self.assertNotIn('private path', body['error'])
        self.assertEqual(self.server.store.rooms, {})
        self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])
        host = self.room()
        code = host['code']
        room = self.server.store.rooms[code]
        for endpoint, payload, token in [('/api/join', dict(room=code, name='ghost'), '')]:
            before = copy.deepcopy(room)
            with patch('server.web.os.replace', side_effect=OSError('disk full')):
                self.assertEqual(self.request(endpoint, payload, token)[0], 503)
            self.assertEqual(room, before)
        player = self.request('/api/join', dict(room=code, name='real'))[1]
        for endpoint, payload, token in [
            ('/api/player', dict(room=code, kind='ready'), player['token']),
            ('/api/host', dict(room=code, kind='kick', player=player['id']), host['token']),
        ]:
            before = copy.deepcopy(room)
            saved = (Path(self.temp.name) / f'room-{code}.json').read_bytes()
            with patch('server.web.os.replace', side_effect=PermissionError('locked')):
                self.assertEqual(self.request(endpoint, payload, token)[0], 503)
            self.assertEqual(room, before)
            self.assertEqual((Path(self.temp.name) / f'room-{code}.json').read_bytes(), saved)
            self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])
        self.assertEqual(
            self.request('/api/player', dict(room=code, kind='ready'), player['token'])[0], 200
        )

    def test_malformed_json_and_unknown_routes_are_friendly(self):
        req = urllib.request.Request(
            self.base + '/api/join', data=b'{not json', headers={'Content-Type': 'application/json'}
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        with caught.exception as error:
            self.assertEqual(error.code, 400)
            self.assertEqual(json.loads(error.read()), {'error': '请求格式不正确。'})
        for code in ('', '?room=ZZZZZ'):
            self.assertEqual(self.request('/api/whatever' + code), (404, {'error': '没有此接口。'}))
        self.assertEqual(self.request('/api/join')[0], 405)

    def test_failed_choice_does_not_consume_sequence_and_can_be_retried(self):
        host = self.room()
        code = host['code']
        players = []
        for i in range(3):
            player = self.request('/api/join', dict(room=code, name=str(i)))[1]
            players.append(player)
            self.request('/api/player', dict(room=code, kind='ready'), player['token'])
        self.request('/api/host', dict(room=code, kind='setup'), host['token'])
        me = self.request(f'/api/state?room={code}', token=players[0]['token'])[1]['me']
        payload = dict(room=code, kind='wait', seq=1, version=me['version'], batch=me['batch'])
        room = self.server.store.rooms[code]
        before = copy.deepcopy(room)
        with patch('server.web.os.replace', side_effect=PermissionError('locked')):
            self.assertEqual(self.request('/api/player', payload, players[0]['token'])[0], 503)
        self.assertEqual(room, before)
        status, result = self.request('/api/player', payload, players[0]['token'])
        self.assertEqual(status, 200)
        self.assertEqual(result['me']['lastSeq'], 1)
        self.assertTrue(result['me']['submitted'])

    def test_join_ready_setup_and_privacy_authorization(self):
        host = self.room()
        code = host['code']
        players = []
        for i in range(6):
            status, p = self.request('/api/join', dict(room=code, name=f'玩家{i}'))
            self.assertEqual(status, 200)
            players.append(p)
            self.request('/api/player', dict(room=code, kind='ready'), p['token'])
        self.assertEqual(self.request('/api/join', dict(room=code, name='第七人'))[0], 400)
        self.assertEqual(
            self.request('/api/host', dict(room=code, kind='setup'), players[0]['token'])[0], 403
        )
        self.assertEqual(
            self.request('/api/host', dict(room=code, kind='setup', template='S05'), host['token'])[
                0
            ],
            200,
        )
        for auth in ('', host['token']):
            state = self.request(f'/api/state?room={code}', token=auth)[1]
            self.assertNotIn('me', state)
            self.assertEqual(sum(p['role'] is not None for p in state['players']), 1)
            for private in ('seed', 'hostToken', 'events', 'attempts', 'hand', 'protectionUntil'):
                self.assertNotIn(private, json.dumps(state))
        first = self.request(f'/api/state?room={code}', token=players[0]['token'])[1]
        self.assertEqual(first['me']['id'], players[0]['id'])
        self.assertIn(first['me']['role'], ('renko', 'maribel', 'yukari', 'sumireko'))
        status, conflict = self.request(
            '/api/player',
            dict(room=code, kind='wait', seq=1, version=-1, batch=first['me']['batch']),
            players[0]['token'],
        )
        self.assertEqual((status, conflict.get('code')), (409, 'stale_action'))
        self.assertEqual(self.request('/favicon.ico')[0], 200)
        self.assertEqual(self.request(f'/api/state?room={code}', token='wrong')[0], 401)
        self.assertEqual(self.request(f'/api/export?room={code}', token=host['token'])[0], 400)
        self.assertEqual(
            self.request(f'/api/export?room={code}', token=players[0]['token'])[0], 403
        )

    def test_no_cross_origin_no_path_traversal_and_no_runtime_files(self):
        self.assertEqual(
            self.request('/api/rooms', {}, extra={'Origin': 'https://evil.example'})[0], 403
        )
        self.assertEqual(self.request('/api/rooms', {}, extra={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/%2e%2e/server/game.py')[0], 404)
        self.assertEqual(self.request('/runtime/room-X.json')[0], 404)

    def test_restart_aborts_live_game_and_preserves_credentials(self):
        host = self.room()
        code = host['code']
        for i in range(3):
            p = self.request('/api/join', dict(room=code, name=f'玩家{i}'))[1]
            self.request('/api/player', dict(room=code, kind='ready'), p['token'])
        self.request('/api/host', dict(room=code, kind='setup'), host['token'])
        restored = Store(Path(self.temp.name)).rooms[code]
        self.assertEqual(restored['result']['outcome'], 'aborted')
        self.assertEqual(restored['hostToken'], host['token'])
        self.assertEqual(restored['players'][2]['token'], p['token'])

    def test_rematch_keeps_seats_resets_roles_and_kick_revokes_token(self):
        host = self.room()
        code = host['code']
        p = self.request('/api/join', dict(room=code, name='旅人'))[1]
        self.request('/api/host', dict(room=code, kind='abort'), host['token'])
        self.request('/api/host', dict(room=code, kind='rematch'), host['token'])
        state = self.request(f'/api/state?room={code}', token=p['token'])[1]
        self.assertEqual(state['phase'], 'lobby')
        self.assertEqual(state['me']['id'], p['id'])
        self.request('/api/host', dict(room=code, kind='kick', player=p['id']), host['token'])
        self.assertEqual(self.request(f'/api/state?room={code}', token=p['token'])[0], 401)

    def test_completed_rounds_are_archived_before_rematch_and_match_export(self):
        host = self.room()
        code = host['code']
        players = []
        for i in range(3):
            p = self.request('/api/join', dict(room=code, name=f'P{i}'))[1]
            players.append(p)
        logs = Path(self.temp.name) / 'logs'
        original = None
        for round_number in (1, 2):
            for p in players:
                self.request('/api/player', dict(room=code, kind='ready'), p['token'])
            self.assertEqual(
                self.request('/api/host', dict(room=code, kind='setup'), host['token'])[0], 200
            )
            self.assertEqual(
                self.request(
                    '/api/host', dict(room=code, kind='launch', force=True), host['token']
                )[0],
                200,
            )
            self.assertEqual(
                self.request('/api/host', dict(room=code, kind='abort'), host['token'])[0], 200
            )
            files = list(logs.glob('*.json'))
            self.assertEqual(len(files), round_number)
            exported = self.request(f'/api/export?room={code}', token=host['token'])[1]
            current = next(path for path in files if exported['roundId'] in path.name)
            self.assertEqual(json.loads(current.read_text(encoding='utf-8')), exported)
            self.assertIsNotNone(exported['timing']['startedAt'])
            self.assertIsNotNone(exported['timing']['durationSeconds'])
            self.assertNotIn(host['token'], current.read_text(encoding='utf-8'))
            if original:
                self.assertEqual(original[0].read_bytes(), original[1])
                self.assertEqual(exported['previousRoundId'], original[2])
            else:
                original = (current, current.read_bytes(), exported['roundId'])
                self.assertEqual(
                    self.request('/api/host', dict(room=code, kind='rematch'), host['token'])[0],
                    200,
                )
        self.assertEqual(self.request('/runtime/logs/' + current.name)[0], 404)

    def test_archive_failure_prevents_overwriting_round_on_rematch(self):
        from server.web import SaveError

        host = self.room()
        code = host['code']
        store = self.server.store
        original = store.write_json

        def fail_archive(path, value):
            if path.parent.name == 'logs':
                raise SaveError('archive unavailable')
            original(path, value)

        with patch.object(store, 'write_json', side_effect=fail_archive):
            self.assertEqual(
                self.request('/api/host', dict(room=code, kind='abort'), host['token'])[0], 200
            )
            self.assertEqual(
                self.request('/api/host', dict(room=code, kind='rematch'), host['token'])[0], 503
            )
            self.assertEqual(store.rooms[code]['phase'], 'ended')
            saved = json.loads(
                (Path(self.temp.name) / f'room-{code}.json').read_text(encoding='utf-8')
            )
            self.assertEqual(saved['phase'], 'ended')
        self.assertEqual(
            self.request('/api/host', dict(room=code, kind='rematch'), host['token'])[0], 200
        )
        self.assertEqual(len(list((Path(self.temp.name) / 'logs').glob('*.json'))), 1)

    def test_poll_triggered_timeout_is_archived_without_shutdown(self):
        from test_game import prepared

        room, *_ = prepared()
        self.server.store.rooms[room['code']] = room
        with patch('server.web.now_ms', return_value=100000):
            status, state = self.request('/api/state?room=TEST1')
        self.assertEqual(status, 200)
        self.assertEqual(state['result']['outcome'], 'aborted')
        logs = list((Path(self.temp.name) / 'logs').glob('*.json'))
        self.assertEqual(len(logs), 1)
        data = json.loads(logs[0].read_text(encoding='utf-8'))
        self.assertEqual(data['timing']['durationSeconds'], 90)


if __name__ == '__main__':
    unittest.main()
