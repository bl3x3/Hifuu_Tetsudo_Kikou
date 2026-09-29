import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import game, journal
from server.web import Store, SaveError
from test_game import prepared, command


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.wall = patch('server.journal.wall_now', return_value='2026-09-28T16:00:00.000+08:00')
        self.wall.start()
        self.addCleanup(self.wall.stop)

    def advance(self, room, milliseconds):
        now=room['updated']+milliseconds
        room['hostSeen']=now
        for p in room['players']:p['seen']=now
        game.advance(room,now)

    def test_calendar_duration_and_phase_breakdown(self):
        room,r,m,y=prepared()
        room['phase']='briefing'
        self.advance(room,5000)
        game.open_decision(room,room['players'],initial=True)
        for p in room['players']:command(room,p,'wait')
        game.launch(room,room['updated'])
        self.advance(room,2000)
        game.open_decision(room,[r])
        self.advance(room,4000)
        game.pause_decision(room,True)
        self.advance(room,2000)
        game.pause_decision(room,False)
        command(room,r,'wait')
        self.advance(room,3000)
        game.end(room,'aborted','test')
        data=journal.record(room)
        timing=data['timing']
        self.assertEqual(timing['startedAt'],'2026-09-28T16:00:00.000+08:00')
        self.assertEqual(timing['launchedAt'],'2026-09-28T16:00:05.000+08:00')
        self.assertEqual(timing['endedAt'],'2026-09-28T16:00:16.000+08:00')
        self.assertEqual(timing['durationSeconds'],16)
        self.assertEqual(timing['launchedDurationSeconds'],11)
        self.assertEqual(timing['startDisplayTime'],'第1日 16:00')
        self.assertEqual(timing['phaseSeconds'],dict(briefing=5,running=2,decision=7,hostPaused=2,technical=0))
        self.assertEqual(sum(timing['phaseSeconds'].values()),timing['durationSeconds'])
        self.assertEqual(data['summary']['autoContinues'],1)
        self.assertEqual(data['decisionWindows'][-1]['submissions'][0]['afterSeconds'],6)
        before=copy.deepcopy(data)
        game.advance(room,room['updated']+999999)
        self.assertEqual(journal.record(room),before)

    def test_technical_time_and_disconnected_seats(self):
        room,r,m,y=prepared()
        game.advance(room,16000)
        self.advance(room,1000)
        self.advance(room,3000)
        game.end(room,'aborted','test')
        data=journal.record(room)
        self.assertEqual(data['timing']['phaseSeconds']['technical'],10)
        self.assertEqual(data['summary']['disconnectCount'],1)
        self.assertEqual(data['summary']['recoveredDisconnectCount'],1)
        outage=next(e for e in data['events'] if e['type']=='technical_pause')
        self.assertEqual(outage['missingPlayers'],[r['id'],m['id'],y['id']])
        self.assertTrue(outage['hostMissing'])

    def test_start_time_uses_new_wall_anchor_after_lobby_restart(self):
        room=game.create_room('CLOCK',10000)
        for name in ('A','B','C'):game.join(room,name,10000)['ready']=True
        # A restarted computer can have an unrelated monotonic clock origin.
        with patch('server.journal.wall_now',return_value='2026-09-29T09:12:00.000+08:00'):
            for p in room['players']:p['seen']=100
            game.setup(room,'S04',100)
        self.advance(room,1000)
        game.end(room,'aborted','test')
        timing=journal.record(room)['timing']
        self.assertEqual(timing['createdAt'],'2026-09-28T16:00:00.000+08:00')
        self.assertEqual(timing['startedAt'],'2026-09-29T09:12:00.000+08:00')
        self.assertEqual(timing['endedAt'],'2026-09-29T09:12:01.000+08:00')
        self.assertEqual(timing['durationSeconds'],1)

    def test_summary_choices_changes_routes_and_no_credentials(self):
        room,r,m,y=prepared()
        r['station']='s023'
        game.deal(room,r)
        old=r['hand'][0]['lineId']
        option=next(o for o in game.private_state(room,r,10000)['me']['availableLines'] if o['id']!=old)
        command(room,r,'change',slot=0,edge=option['edge'])
        game.open_decision(room,[r,m])
        command(room,r,'travel',card=r['hand'][0]['id'])
        packet=dict(kind='travel',seq=r['lastSeq'],version=r['actionVersion'],batch=room['decision']['id'])
        game.action(room,r,packet)
        game.continue_decision(room,force=True)
        game.end(room,'aborted','test')
        data=journal.record(room)
        self.assertEqual(data['players'][0]['changes'],1)
        self.assertEqual(data['players'][0]['travelChoices'],1)
        self.assertEqual(data['players'][1]['hostForcedWaits'],1)
        choice=next(e for e in data['events'] if e['type']=='choice_submitted' and e['choice']=='travel')
        self.assertEqual(choice['line'],option['id'])
        self.assertGreater(len(choice['route']),1)
        encoded=json.dumps(data)
        for secret in [room['hostToken']]+[p['token'] for p in room['players']]:
            self.assertNotIn(secret,encoded)

    def test_loop_writes_end_before_periodic_save_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            room,*_=prepared()
            store.rooms[room['code']]=room
            store.stopped=Mock()
            store.stopped.wait.side_effect=[False,True]
            with patch('server.web.now_ms',return_value=100), patch('server.web.game.advance',side_effect=lambda r,n:game.end(r,'hifuu','test')):
                store.loop()
            files=list((Path(directory)/'logs').glob('*.json'))
            self.assertEqual(len(files),1)
            saved=files[0].read_bytes()
            timestamp=files[0].stat().st_mtime_ns
            store.save(room)
            self.assertEqual(files[0].read_bytes(),saved)
            self.assertEqual(files[0].stat().st_mtime_ns,timestamp)
            self.assertEqual(list(Path(directory).rglob('*.tmp')),[])

    def test_archive_failure_retains_snapshot_and_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            room,*_=prepared()
            game.end(room,'aborted','test')
            original=store.write_json
            def fail_archive(path,value):
                if path.parent.name=='logs':raise SaveError('archive disk full')
                original(path,value)
            with patch.object(store,'write_json',side_effect=fail_archive):
                store.save(room)
            snapshot=json.loads((Path(directory)/'room-TEST1.json').read_text(encoding='utf-8'))
            self.assertEqual(snapshot['phase'],'ended')
            self.assertTrue(store.archive_errors)
            store.save(room)
            self.assertFalse(store.archive_errors)
            self.assertEqual(len(list((Path(directory)/'logs').glob('*.json'))),1)

    def test_restart_marks_incomplete_time_and_legacy_does_not_invent_it(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory))
                room,*_=prepared()
                self.advance(room,1500)
                if legacy:room.pop('journal')
                store.save(room)
                restored=Store(Path(directory))
                data=journal.record(restored.rooms[room['code']])
                self.assertIsNone(data['timing']['durationSeconds'])
                self.assertIsNone(data['timing']['endedAt'])
                self.assertEqual(data['timing']['timeQuality'],'legacy-unknown' if legacy else 'interrupted')
                self.assertEqual(data['timing']['observedDurationSeconds'],None if legacy else 1.5)
                self.assertEqual(len(list((Path(directory)/'logs').glob('*.json'))),1)
                Store(Path(directory))
                self.assertEqual(len(list((Path(directory)/'logs').glob('*.json'))),1)


if __name__=='__main__':unittest.main()
