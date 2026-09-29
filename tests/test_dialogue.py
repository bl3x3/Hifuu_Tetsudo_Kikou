"""Public memory rules, privacy, and actual arrival integration."""
import copy
import json
import unittest
from unittest.mock import patch
from server import game
from server.dialogue import QUOTES, candidates
from test_game import prepared, entry, travel, multi_stop_trip


class DialogueTest(unittest.TestCase):
    def setUp(self):
        self.room, self.r, self.m, self.y = prepared()
        game.set_dialogue_enabled(self.room, True)
        self.room['tick'] = 32

    def emit(self, player, station, uid, value=0, **kwargs):
        e = entry(player, station, uid)
        e.update(kwargs)
        game.resolve_dialogues(self.room, [e], lambda _: value)

    def test_midnight_boundary_and_no_retroactive_station_trigger(self):
        self.room['tick'] = 31
        self.emit(self.m, 's060', 100)
        self.assertEqual(self.room['dialogues'], [])
        self.assertEqual(self.room['dialogueProgress'], {})
        self.room['tick'] = 32
        game.resolve_dialogues(self.room, [])
        self.assertEqual(self.room['dialogues'], [])
        self.emit(self.m, 's060', 101)
        self.assertEqual(self.room['dialogues'][0]['tick'], 32)

    def test_off_renko_and_technical_pause_do_not_consume(self):
        game.set_dialogue_enabled(self.room, False)
        self.emit(self.m, 's060', 100)
        game.set_dialogue_enabled(self.room, True)
        self.emit(self.r, 's060', 102)
        self.room['technical'] = dict(resumeAt=None)
        self.emit(self.m, 's060', 103)
        self.assertEqual(self.room['dialogueProgress'], {})
        self.room['technical'] = None
        self.emit(self.m, 's060', 104)
        self.assertEqual(len(self.room['dialogues']), 1)

    def test_new_locations_allow_passing_and_keep_role_specific_pools(self):
        locations = [('maribel', 's037', 'm_matsumoto'), ('maribel', 's029', 'm_mito'),
                     ('maribel', 's096', 'm_south_kyushu'), ('maribel', 's097', 'm_south_kyushu'),
                     ('maribel', 's051', 'm_fuji'), ('maribel', 's052', 'm_fuji'),
                     ('yukari', 's068', 'y_fukuchiyama')]
        for role, station, quote in locations:
            with self.subTest(role=role, station=station):
                self.setUp()
                player = self.m if role == 'maribel' else self.y
                self.emit(player, station, 100, stop=False)
                self.assertEqual(self.room['dialogues'][0]['quote'], quote)
                self.assertEqual(candidates(self.r, entry(self.r, station, 101)), [])

    def test_variants_share_one_roll_and_select_both_ends_of_pool(self):
        pools = [('maribel', 's029', 'm_mito', 'm_mito_reality'),
                 ('maribel', 's060', 'm_kyoto', 'm_kyoto_dream'),
                 ('yukari', 's060', 'y_kyoto', 'y_kyoto_boundary'),
                 ('yukari', 's051', 'y_fuji', 'y_fuji_fear')]
        for role, station, first, last in pools:
            for pick, expected in [(0, first), (.999999, last)]:
                with self.subTest(role=role, station=station, pick=pick):
                    self.setUp()
                    player = self.m if role == 'maribel' else self.y
                    calls = []
                    def roller(key):
                        calls.append(key)
                        return pick if key.endswith(':quote') else 0
                    game.resolve_dialogues(self.room, [entry(player, station, 100, stop=False)], roller)
                    self.assertEqual(self.room['dialogues'][0]['quote'], expected)
                    self.assertEqual(calls, [f"dialogue:{player['id']}:100", f"dialogue:{player['id']}:100:quote"])
                    self.assertEqual(len([e for e in self.room['events'] if e['type'] == 'roll']), 1)
                    self.room['tick'] += 8
                    self.emit(player, station, 101)
                    self.assertEqual(len(self.room['dialogues']), 1)

    def test_failed_variant_group_does_not_reroll_and_south_kyushu_is_shared(self):
        calls = []
        def fail(key):
            calls.append(key)
            return .9
        game.resolve_dialogues(self.room, [entry(self.m, 's029', 100, stop=False)], fail)
        self.assertEqual(calls, [f"dialogue:{self.m['id']}:100"])
        self.emit(self.m, 's029', 101)
        self.emit(self.m, 's096', 102, .9, stop=False)
        self.emit(self.m, 's097', 103)
        self.assertEqual(self.room['dialogues'], [])
        self.assertEqual(self.room['dialogueProgress'][str(self.m['id'])]['misses'], 2)

    def test_real_nonstop_pass_speaks_without_stopping_or_opening_decision(self):
        multi_stop_trip(self.room, self.m, remaining=1)
        self.m['trip']['card']['stops'][1] = False
        self.room['tick'] = 31
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(self.room)
        self.assertEqual(self.room['dialogues'][0]['quote'], 'm_kyoto')
        self.assertEqual(self.room['dialogues'][0]['tick'], 32)
        self.assertIsNone(self.m['station'])
        self.assertEqual(self.m['trip']['segment'], 1)
        self.assertEqual(self.room['phase'], 'running')
        self.assertIsNone(self.room['decision'])
        self.assertTrue(any(e['type'] == 'pass' and e['station'] == 's060' for e in self.room['events']))

    def test_variant_sources_and_seeded_roundtrip(self):
        other = json.loads(json.dumps(self.room))
        self.m['corrupted'] = True
        other['players'][1]['corrupted'] = True
        game.resolve_dialogues(self.room, [entry(self.m, 's029', 100)])
        game.resolve_dialogues(other, [entry(other['players'][1], 's029', 100)])
        self.assertEqual(other, self.room)
        # Every excerpt can be projected using its saved ID, including older excerpts.
        self.room['dialogues'] = [dict(id=i, tick=32, player=self.m['id'], quote=key)
                                  for i, key in enumerate(QUOTES)]
        public = game.public_state(self.room, 10000)['publicDialogues']
        for item in public:
            self.assertEqual(set(item), {'id', 'tick', 'player', 'text'})
        game.end(self.room, 'aborted', 'test')
        done = game.public_state(self.room, 10000)['publicDialogues']
        self.assertEqual(len(done), 16)
        for item in done:
            self.assertTrue(item['source'])
            self.assertTrue(item['sourceUrl'].startswith('https://thbwiki.cc/'))
            self.assertTrue(item['mapping'])

    def test_escalation_and_independent_characters(self):
        self.emit(self.m, 's060', 100, .4)
        self.emit(self.m, 's039', 101, .7)
        self.emit(self.y, 's060', 102, .4)
        self.emit(self.m, 's063', 103, .999999)
        rolls = [e for e in self.room['events'] if e['type'] == 'roll']
        self.assertEqual([e['probability'] for e in rolls], [.4, .7, .4, 1.0])
        self.assertEqual([e['success'] for e in rolls], [False, False, False, True])

    def test_failed_group_and_toggle_cannot_be_farmed(self):
        self.emit(self.y, 's051', 100, .9)
        before = copy.deepcopy(self.room['dialogueProgress'])
        game.set_dialogue_enabled(self.room, False)
        game.set_dialogue_enabled(self.room, True)
        self.emit(self.y, 's052', 101)
        self.emit(self.y, 's051', 100)
        self.assertEqual(self.room['dialogueProgress'], before)
        self.assertEqual(self.room['dialogues'], [])

    def test_cooldown_cap_and_post_success_probability(self):
        self.emit(self.m, 's060', 100)
        self.room['tick'] = 39
        self.emit(self.m, 's039', 101)
        self.assertNotIn('nagano', self.room['dialogueProgress'][str(self.m['id'])]['groups'])
        self.room['tick'] = 40
        self.emit(self.m, 's039', 102, .4)
        self.emit(self.m, 's063', 103, .39)
        self.room['tick'] = 48
        self.emit(self.m, 's023', 104, edge='e131')
        self.assertEqual(len(self.room['dialogues']), 2)

    def test_express_priority_and_replay_deduplication(self):
        self.emit(self.m, 's023', 100)  # An ordinary Tokyo arrival is not the express.
        self.assertEqual(self.room['dialogues'], [])
        self.emit(self.m, 's060', 101, edge='e131')
        self.assertEqual(self.room['dialogues'][0]['quote'], 'm_express')
        self.room['tick'] = 40
        self.emit(self.m, 's060', 101, edge='e131')
        self.assertEqual(len(self.room['dialogues']), 1)
        self.emit(self.m, 's023', 102, edge='e131')
        self.assertEqual(len(self.room['dialogues']), 1)
        self.emit(self.m, 's060', 103)
        self.assertEqual(self.room['dialogues'][-1]['quote'], 'm_kyoto')

    def test_same_tick_each_role_can_speak_and_corruption_keeps_pool(self):
        self.m['corrupted'] = True
        game.resolve_dialogues(self.room, [entry(self.m, 's060', 100), entry(self.y, 's060', 101)], lambda _: 0)
        self.assertEqual([e['quote'] for e in self.room['dialogues']], ['m_kyoto', 'y_kyoto'])
        self.assertTrue(self.m['corrupted'])

    def test_public_projection_and_ended_sources(self):
        self.emit(self.m, 's060', 100)
        public = game.public_state(self.room, 10000)
        self.assertEqual(set(public['publicDialogues'][0]), {'id', 'tick', 'player', 'text'})
        self.assertIsNone(public['players'][1]['role'])
        for hidden in ('m_kyoto', 'dialogueProgress', 'probability', 'sourceUrl', 'quote'):
            self.assertNotIn(hidden, json.dumps(public))
        self.assertEqual(game.private_state(self.room, self.r, 10000)['publicDialogues'], public['publicDialogues'])
        game.end(self.room, 'aborted', 'test')
        done = game.public_state(self.room, 10000)['publicDialogues'][0]
        self.assertIn('莲台野夜行', done['source'])
        self.assertTrue(done['sourceUrl'].startswith('https://thbwiki.cc/'))

    def test_json_roundtrip_and_legacy_defaults(self):
        self.emit(self.m, 's060', 100, .9)
        self.room = json.loads(json.dumps(self.room))
        self.emit(self.m, 's060', 101)
        self.emit(self.m, 's039', 102, .69)
        self.assertEqual(len(self.room['dialogues']), 1)
        for field in ('dialogueEnabled', 'dialogueProgress', 'dialogues'):
            self.room.pop(field)
        state = game.public_state(self.room, 10000)
        self.assertFalse(state['dialogueEnabled'])
        self.assertEqual(state['publicDialogues'], [])

    def test_real_express_and_intermediate_arrivals(self):
        travel(self.room, self.m, 's023', 's060', remaining=1)
        self.room['tick'] = 31
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(self.room)
        self.assertEqual(self.room['dialogues'][0]['quote'], 'm_express')
        self.assertEqual(self.room['phase'], 'decision')
        room, r, m, y = prepared()
        game.set_dialogue_enabled(room, True)
        room['tick'] = 31
        multi_stop_trip(room, m, remaining=1)
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(room)
        self.assertEqual(room['dialogues'][0]['quote'], 'm_kyoto')
        self.assertEqual(room['phase'], 'running')  # The dialogue does not open a decision.

    def test_meeting_and_timeout_win_before_dialogue(self):
        self.r['station'] = 's060'
        travel(self.room, self.m, 's023', 's060', remaining=1)
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(self.room)
        self.assertEqual(self.room['result']['outcome'], 'hifuu')
        self.assertEqual(self.room['dialogues'], [])
        room, r, m, y = prepared()
        game.set_dialogue_enabled(room, True)
        room['tick'] = game.JOURNEY_LIMIT_TICKS
        travel(room, m, 's023', 's060', remaining=1)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'], 'draw')
        self.assertEqual(room['dialogues'], [])

    def test_setting_validation_and_seeded_reproducibility(self):
        for value in (1, 'true', None):
            with self.assertRaises(ValueError):
                game.set_dialogue_enabled(self.room, value)
        other = copy.deepcopy(self.room)
        game.resolve_dialogues(self.room, [entry(self.m, 's060', 100)])
        game.resolve_dialogues(other, [entry(other['players'][1], 's060', 100)])
        self.assertEqual(other, self.room)
        game.end(self.room, 'aborted', 'test')
        with self.assertRaises(ValueError):
            game.set_dialogue_enabled(self.room, True)
