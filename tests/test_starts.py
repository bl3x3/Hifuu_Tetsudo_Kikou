import unittest
from itertools import combinations
from server.data import STATIONS, TEMPLATES, ADJACENCY
from server.starts import distances_from, MIN_START_DISTANCE, MAX_START_DISTANCE
from server import game


class StartingPositionsTest(unittest.TestCase):
    def test_all_templates_spread_with_express_shortcut_included(self):
        by_id = {s['id']: s for s in STATIONS}
        self.assertEqual(len(TEMPLATES), 12)
        self.assertEqual(distances_from('s023', ADJACENCY)['s060'], 4)
        for template in TEMPLATES:
            ids = [template[f'station_{c}_id'] for c in 'abc']
            self.assertEqual(len({by_id[s]['region'] for s in ids}), 3)
            pairs = [distances_from(a, ADJACENCY)[b] for a, b in combinations(ids, 2)]
            self.assertGreaterEqual(min(pairs), MIN_START_DISTANCE)
            self.assertLessEqual(max(pairs), MAX_START_DISTANCE)
            self.assertLessEqual(max(pairs)/min(pairs), 1.6)
            self.assertEqual(pairs, [template[f'pair_{p}_ticks'] for p in ('ab','ac','bc')])

    def test_random_and_selected_starts_use_spread_pool(self):
        for seed in range(36):
            room = game.create_room('START', 10000)
            room['seed'] = str(seed)
            for name in ('一','二','三'):
                game.join(room, name, 10000)['ready'] = True
            selection = 'random' if seed < 24 else f'S{seed-23:02}'
            game.setup(room, selection, 10000)
            self.assertEqual({p['role'] for p in room['players']}, {'renko','maribel','yukari'})
            for a,b in combinations(room['players'],2):
                self.assertGreaterEqual(distances_from(a['station'],ADJACENCY)[b['station']],20)


if __name__ == '__main__':
    unittest.main()
