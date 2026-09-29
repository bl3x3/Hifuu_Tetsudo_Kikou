"""Configuration failures and runtime use of authored values."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import game, settings
from server.data import EDGE_BY_ID, STATION_BY_ID
from server.dialogue import QUOTES
from test_game import prepared, entry


class SettingsTest(unittest.TestCase):
    def test_rule_errors_identify_the_field(self):
        invalid = (
            (('tick_ms',), 0),
            (('tick_ms',), True),
            (('dialogue', 'first_probabilities'), []),
            (('dialogue', 'repeat_probability'), 1.1),
            (('events', 'nagano_probability'), -0.1),
            (('dialogue', 'cooldown_ticks'), 1.5),
        )
        for path, value in invalid:
            with self.subTest(path=path, value=value):
                rules = copy.deepcopy(settings.RULES)
                target = rules
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(ValueError, path[-1]):
                    settings.validate_rules(rules)

    def test_unknown_fields_and_missing_messages_fail_at_startup(self):
        rules = copy.deepcopy(settings.RULES)
        rules['typo'] = 1
        with self.assertRaisesRegex(ValueError, 'rules.json'):
            settings.validate_rules(rules)
        messages = dict(settings.MESSAGES)
        del messages['nara_bonus']
        with self.assertRaisesRegex(ValueError, 'messages.json'):
            settings.validate_messages(messages)

    def test_dialogue_references_and_source_links_are_validated(self):
        for field, value in [
            ('stations', ['missing']),
            ('edge', 'missing'),
            ('url', 'javascript:alert(1)'),
            ('role', 'unknown'),
        ]:
            with self.subTest(field=field):
                quotes = copy.deepcopy(QUOTES)
                quotes['m_kyoto'][field] = value
                with patch.object(settings, 'load_json', return_value=quotes):
                    with self.assertRaisesRegex(ValueError, field):
                        settings.load_dialogues(STATION_BY_ID, EDGE_BY_ID)

    def test_start_groups_need_three_distinct_stations(self):
        starts = copy.deepcopy(settings.STARTS)
        starts['groups'][0] = ['s060', 's060', 's023']
        with self.assertRaisesRegex(ValueError, 'groups'):
            settings.validate_starts(starts)

    def test_missing_and_malformed_json_have_file_context(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, 'CONFIG_DIR', Path(directory)):
                with self.assertRaisesRegex(ValueError, 'rules.json'):
                    settings.load_json('rules.json')
                Path(directory, 'rules.json').write_text('{', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'rules.json'):
                    settings.load_json('rules.json')

    def test_configured_speed_probability_and_message_reach_the_engine(self):
        with patch.dict(game.RULES, tick_ms=1234):
            self.assertEqual(game.create_room('SPEED', 0)['tickMs'], 1234)
        room, renko, maribel, _ = prepared()
        room['tick'] = 8
        with patch.dict(game.EVENT_RULES, kyoto_dialogue_probability=1):
            with patch.dict(game.MESSAGES, kyoto_dialogue='自定义对白'):
                game.resolve_events(room, [entry(maribel, 's060', 101)], lambda _: 0.99)
        self.assertEqual(renko['messages'][-1]['text'], '自定义对白')

    def test_custom_dialogue_probability_ladder_and_limit(self):
        room, _, maribel, _ = prepared()
        room['tick'] = game.DIALOGUE_START_TICK
        game.set_dialogue_enabled(room, True)
        with patch.dict(game.DIALOGUE_RULES, first_probabilities=[1], max_per_player=1):
            game.resolve_dialogues(room, [entry(maribel, 's060', 201)], lambda _: 0.99)
            room['tick'] += 10
            game.resolve_dialogues(room, [entry(maribel, 's039', 202)], lambda _: 0)
        self.assertEqual(len(room['dialogues']), 1)
