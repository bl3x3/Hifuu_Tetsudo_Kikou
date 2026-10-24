"""Hat deception, secret contact effects and endpoint-safe projections."""

import copy
import json
import unittest
from unittest.mock import patch

from server import game
from server.data import ADJACENCY
from test_game import command, entry, multi_stop_trip, travel


def prepared(count=6):
    room = game.create_room('HATS', 10000)
    for i in range(count):
        game.join(room, f'旅人{i + 1}', 10000)['ready'] = True
    game.setup(room, 'S04', 10000)
    roles = game.by_role(room)
    r, m, y = (roles[k] for k in ('renko', 'maribel', 'yukari'))
    extras = [p for p in room['players'] if p['role'] == 'sumireko']
    for p, station in zip([r, m, y] + extras, ['s001', 's055', 's088', 's060', 's063', 's059']):
        p.update(
            station=station,
            lastArrival=dict(station=station, tick=0),
            lastSighting=dict(station=station, tick=0),
        )
        game.deal(room, p)
    room.update(phase='running', decision=None)
    return room, r, m, y, extras


class SumirekoTest(unittest.TestCase):
    def test_expanded_matches_reach_valid_endings(self):
        for count in (4, 5, 6):
            for seed in range(3):
                room, r, m, y, extras = prepared(count)
                room['seed'] = f'expanded:{count}:{seed}'
                room['dialogueEnabled'] = True
                for _ in range(193):
                    if room['result']:
                        break
                    for p in room['players']:
                        encounter = game.private_state(room, p, 10000)['me'].get('encounter')
                        if encounter and not encounter['confirmed']:
                            command(room, p, 'confirm_encounter', encounter=encounter['id'])
                        if game.can_act(room, p):
                            command(room, p, 'travel', card=p['hand'][0]['id'])
                    if room['decision']:
                        game.continue_decision(room)
                    game.rail_step(room)
                    json.dumps(game.public_state(room, 10000))
                self.assertIsNotNone(room['result'])
                self.assertEqual(len(room['result']['individuals']), count - 3)
                self.assertEqual(sum(p['id'] == room['hat']['holder'] for p in room['players']), 1)

    def loan(self, room, r, s):
        with patch.object(game, 'random_value', return_value=0):
            encounter = game.resolve_hat(room, [entry(r, s['station'], room['serial'] + 1)])
        self.assertEqual(room['hat']['holder'], s['id'])
        self.assertTrue(encounter['changed'])
        return encounter

    def test_three_to_six_players_and_distinct_starts(self):
        for count in range(3, 7):
            room = game.create_room('SIZE', 0)
            for i in range(count):
                game.join(room, str(i), 0)['ready'] = True
            game.setup(room, 'random', 0)
            self.assertEqual(sum(p['role'] == 'sumireko' for p in room['players']), count - 3)
            self.assertEqual(len(set(p['station'] for p in room['players'])), count)
            self.assertEqual(set(game.by_role(room)), {'renko', 'maribel', 'yukari'})
            if count > 3:
                nearby = {game.by_role(room)['renko']['station']}
                for _ in range(2):
                    nearby |= {
                        to
                        for node in tuple(nearby)
                        for edge, to in ADJACENCY[node]
                        if not edge['special']
                    }
                self.assertTrue(
                    any(p['role'] == 'sumireko' and p['station'] in nearby for p in room['players'])
                )
        room = game.create_room('FULL', 0)
        for i in range(6):
            game.join(room, str(i), 0)
        with self.assertRaises(ValueError):
            game.join(room, '7', 0)
        with self.assertRaises(ValueError):
            game.setup(room, 'random', 0)

    def test_both_contacts_return_immediately_but_only_yukari_cancels_win(self):
        for role in ('maribel', 'yukari'):
            for corrupted in (False, True):
                with self.subTest(role=role, corrupted=corrupted):
                    room, r, m, y, extras = prepared()
                    s = extras[0]
                    self.loan(room, r, s)
                    old_expiry = room['hat']['until']
                    room['tick'] = 2
                    m['corrupted'] = corrupted
                    game.by_role(room)[role]['station'] = s['station']
                    game.resolve_contacts(room)
                    self.assertFalse(s['hatEligible'])
                    self.assertEqual(s['winEligible'], role == 'maribel')
                    self.assertEqual(room['hat']['holder'], r['id'])
                    self.assertEqual(room['hat']['cooldownUntil'], 6)
                    self.assertEqual(room['hat']['usesLeft'], 1)
                    self.assertIsNone(room['hat']['until'])
                    self.assertTrue(game.can_act(room, s))
                    self.assertEqual(s['messages'][-1]['text'], '帽子已归还。')
                    room['tick'] = old_expiry
                    game.resolve_contacts(room)
                    game.resolve_hat(room, [])
                    self.assertEqual(room['hat']['cooldownUntil'], 6)
                    self.assertEqual(
                        len([e for e in room['events'] if e['type'] == 'hat_return']), 1
                    )

    def test_contact_states_never_recover_and_multiple_sumirekos_are_independent(self):
        room, r, m, y, extras = prepared()
        a, b, c = extras
        m['station'] = a['station']
        y['station'] = b['station']
        game.resolve_contacts(room)
        self.assertEqual(
            [(p['hatEligible'], p['winEligible']) for p in extras],
            [(False, True), (False, False), (True, True)],
        )
        m['station'], y['station'] = b['station'], a['station']
        game.resolve_contacts(room)
        self.assertFalse(a['winEligible'])
        self.assertFalse(b['winEligible'])
        self.assertTrue(c['winEligible'])

    def test_contact_types_have_identical_public_and_wearer_feedback(self):
        original, r, m, y, extras = prepared()
        self.loan(original, r, extras[0])
        results = []
        for role in ('maribel', 'yukari'):
            room = copy.deepcopy(original)
            s = next(p for p in room['players'] if p['id'] == extras[0]['id'])
            game.by_role(room)[role]['station'] = s['station']
            game.resolve_contacts(room)
            results.append((game.public_state(room, 10000), game.private_state(room, s, 10000)))
        self.assertEqual(results[0], results[1])

    def test_contact_at_expiry_returns_once_and_blocks_same_tick_transfer(self):
        room, r, m, y, extras = prepared()
        self.loan(room, r, extras[0])
        room['tick'] = room['hat']['until']
        m['station'] = y['station'] = extras[0]['station']
        game.resolve_contacts(room)
        game.resolve_hat(room, [entry(r, extras[1]['station'], 100)])
        self.assertEqual(len([e for e in room['events'] if e['type'] == 'hat_return']), 1)
        self.assertFalse(extras[0]['winEligible'])
        self.assertEqual(room['hat']['holder'], r['id'])
        self.assertEqual(room['hat']['cooldownUntil'], room['tick'] + 4)

    def test_duration_cooldown_and_success_only_use_budget(self):
        room, r, m, y, extras = prepared()
        self.loan(room, r, extras[0])
        self.assertNotIn('intent', room['hat'])
        room['tick'] = 7
        game.resolve_hat(room, [])
        self.assertEqual(room['hat']['holder'], extras[0]['id'])
        room['tick'] = 8
        game.resolve_hat(room, [])
        self.assertEqual(room['hat']['holder'], r['id'])
        room['tick'] = 11
        with patch.object(game, 'random_value', return_value=0):
            encounter = game.resolve_hat(room, [entry(r, extras[0]['station'], 101)])
        self.assertFalse(encounter['changed'])
        self.assertEqual(room['hat']['usesLeft'], 1)
        room['tick'] = 12
        game.resolve_hat(room, [])  # Separate before another encounter.
        self.loan(room, r, extras[0])
        room['tick'] = 24
        game.resolve_hat(room, [])
        self.assertEqual(room['hat']['usesLeft'], 0)
        with patch.object(game, 'random_value', return_value=0):
            encounter = game.resolve_hat(room, [entry(r, extras[1]['station'], 102)])
        self.assertFalse(encounter['changed'])

    def test_every_failed_contact_has_same_confirmation_without_a_reason(self):
        projections = []
        for reason in ('coin', 'ineligible', 'cooldown', 'exhausted', 'borrowed'):
            room, r, m, y, extras = prepared()
            s = extras[0]
            r['station'] = s['station']
            game.deal(room, r)
            if reason == 'ineligible':
                s.update(hatEligible=False, winEligible=False)
            elif reason == 'cooldown':
                room['hat']['cooldownUntil'] = 4
            elif reason == 'exhausted':
                room['hat']['usesLeft'] = 0
            elif reason == 'borrowed':
                room['hat'].update(holder=extras[1]['id'], until=8)
            before = copy.deepcopy(room['hat'])
            with patch.object(game, 'random_value', return_value=0.9 if reason == 'coin' else 0):
                encounter = game.resolve_hat(room, [])
            self.assertFalse(encounter['changed'])
            self.assertEqual(set(encounter['participants']), {r['id'], s['id']})
            self.assertEqual(room['hat'], before)
            game.open_decision(room, [], encounter=encounter)
            self.assertEqual(game.public_state(room, 10000)['pendingCount'], 2)
            for p in (r, s):
                private = game.private_state(room, p, 10000)['me']['encounter']
                self.assertEqual(set(private), {'id', 'tick', 'changed', 'confirmed'})
                projections.append(private)
            self.assertNotIn('encounter', game.private_state(room, y, 10000)['me'])
        self.assertTrue(all(p == projections[0] for p in projections))

    def test_manual_hat_presets_are_removed(self):
        room, r, m, y, extras = prepared()
        for p in room['players']:
            for mode in ('off', 'random', 'target'):
                with self.assertRaises(ValueError):
                    command(room, p, 'hat', mode=mode, target=extras[0]['id'])
        for key in ('canSet', 'intent', 'candidates'):
            self.assertNotIn(key, game.private_state(room, r, 10000)['me']['hat'])

    def test_group_selects_once_before_checking_eligibility(self):
        room, r, m, y, extras = prepared()
        r['station'] = 's060'
        for s in extras:
            s['station'] = 's060'
        extras[0]['hatEligible'] = False
        with (
            patch.object(game, 'shuffled', return_value=extras),
            patch.object(game, 'random_value', return_value=0),
        ):
            encounter = game.resolve_hat(room, [])
        self.assertEqual(encounter['target'], extras[0]['id'])
        self.assertFalse(encounter['changed'])
        self.assertEqual(room['hat']['usesLeft'], 2)
        self.assertEqual(set(encounter['participants']), {p['id'] for p in [r] + extras})
        self.assertEqual(room['hat']['holder'], r['id'])

    def test_exact_half_probability_and_only_target_gets_success(self):
        for value, success in ((0, True), (0.499999, True), (0.5, False), (0.999999, False)):
            room, r, m, y, extras = prepared()
            for p in [r] + extras:
                p['station'] = 's060'
                game.deal(room, p)
            with patch.object(game, 'random_value', return_value=value):
                encounter = game.resolve_hat(room, [])
            self.assertEqual(encounter['changed'], success)
            self.assertEqual(room['hat']['usesLeft'], 2 - int(success))
            game.open_decision(room, [], encounter=encounter)
            for p in [r] + extras:
                actual = game.private_state(room, p, 10000)['me']['encounter']['changed']
                self.assertEqual(actual, success and p['id'] in (r['id'], encounter['target']))

    def test_contact_cannot_reroll_until_separated_and_newcomer_is_only_new_target(self):
        room, r, m, y, extras = prepared()
        a, b, _ = extras
        r['station'] = a['station']
        with patch.object(game, 'random_value', return_value=0.9):
            first = game.resolve_hat(room, [])
            self.assertIsNotNone(first)
            for _ in range(4):
                room['tick'] += 1
                self.assertIsNone(game.resolve_hat(room, []))
            snapshot = json.loads(json.dumps(room))
            self.assertIsNone(game.resolve_hat(snapshot, []))
            b['station'] = a['station']
            newcomer = game.resolve_hat(room, [])
            self.assertEqual(newcomer['target'], b['id'])
            self.assertEqual(set(newcomer['participants']), {p['id'] for p in (r, a, b)})
            r['station'] = 's001'
            self.assertIsNone(game.resolve_hat(room, []))
            r['station'] = a['station']
            self.assertIsNotNone(game.resolve_hat(room, []))

    def test_sumireko_only_contact_does_not_pause_or_loan(self):
        room, r, m, y, extras = prepared()
        extras[1]['station'] = extras[0]['station']
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(room)
        self.assertEqual(room['phase'], 'running')
        self.assertIsNone(room['decision'])
        self.assertEqual(room['hat']['holder'], r['id'])

    def test_either_arrival_or_simultaneous_nonstop_pass_starts_contact(self):
        for moving in ('renko', 'sumireko', 'both'):
            room, r, m, y, extras = prepared()
            s = extras[0]
            if moving == 'sumireko':
                r['station'] = s['station']
                multi_stop_trip(room, s, remaining=1)
                s['trip']['card']['stops'][1] = False
            else:
                multi_stop_trip(room, r, remaining=1)
                r['trip']['card']['stops'][1] = False
                if moving == 'both':
                    multi_stop_trip(room, s, remaining=1)
                    s['trip']['card']['stops'][1] = False
            with patch.object(game, 'random_value', return_value=0.9):
                game.rail_step(room)
            self.assertEqual(room['phase'], 'decision')
            self.assertEqual(set(room['decision']['encounter']['participants']), {r['id'], s['id']})
            self.assertEqual(room['decision']['required'], [])
            for p in ([r, s] if moving == 'both' else [r if moving == 'renko' else s]):
                self.assertIsNone(p['station'])
                self.assertIsNotNone(p['trip'])

    def test_nonstop_pass_can_transfer_but_does_not_update_maribel_record(self):
        room, r, m, y, extras = prepared()
        r['lastArrival'] = dict(station='s059', tick=0)
        multi_stop_trip(room, r, remaining=1)
        r['trip']['card']['stops'][1] = False
        with patch.object(game, 'random_value', return_value=0):
            game.rail_step(room)
        self.assertEqual(room['hat']['holder'], extras[0]['id'])
        self.assertIsNone(r['station'])
        self.assertEqual(room['phase'], 'decision')
        self.assertFalse(game.can_act(room, r))
        self.assertEqual(
            game.private_state(room, m, 10000)['me']['renkoLastArrival'],
            dict(station='s059', tick=0),
        )
        self.assertEqual(r['lastSighting']['station'], 's060')

    def test_encounter_requires_both_confirmations_before_time_or_train_can_continue(self):
        room, r, m, y, extras = prepared()
        s = extras[0]
        multi_stop_trip(room, r, remaining=1)
        r['trip']['card']['stops'][1] = False
        with patch.object(game, 'random_value', return_value=0.9):
            game.rail_step(room)
        encounter = room['decision']['encounter']
        trip = copy.deepcopy(r['trip'])
        self.assertEqual(game.public_state(room, 10000)['pendingCount'], 2)
        self.assertFalse(game.public_state(room, 10000)['canForceContinue'])
        with self.assertRaises(ValueError):
            game.continue_decision(room, force=True)
        for p in room['players']:
            p['seen'] = 14000
        room['hostSeen'] = 14000
        game.advance(room, 14000)
        game.rail_step(room)  # Even direct callers cannot advance an unresolved encounter.
        self.assertEqual(room['tick'], 1)
        self.assertEqual(r['trip'], trip)
        command(room, r, 'confirm_encounter', encounter=encounter['id'])
        public = game.public_state(room, 14000)
        self.assertEqual((public['pendingCount'], public['readyCount']), (1, 1))
        self.assertIsNone(room['decision']['remaining'])
        with self.assertRaises(ValueError):
            game.continue_decision(room, force=True)
        command(room, s, 'confirm_encounter', encounter=encounter['id'])
        self.assertEqual(room['decision']['remaining'], game.AUTO_CONTINUE_MS)
        game.continue_decision(room)
        self.assertEqual(room['phase'], 'running')
        self.assertIsNone(r['station'])
        self.assertEqual(r['trip'], trip)
        self.assertEqual(room['tick'], 1)

    def test_route_and_encounter_share_one_batch_without_double_counting_players(self):
        room, r, m, y, extras = prepared()
        s = extras[0]
        travel(room, r, 's059', 's060', remaining=1)
        with patch.object(game, 'random_value', return_value=0.9):
            game.rail_step(room)
        encounter = room['decision']['encounter']
        self.assertEqual(room['decision']['required'], [r['id']])
        self.assertEqual(game.public_state(room, 10000)['pendingCount'], 2)
        command(room, r, 'confirm_encounter', encounter=encounter['id'])
        public = game.public_state(room, 10000)
        self.assertEqual((public['pendingCount'], public['readyCount']), (2, 0))
        self.assertFalse(public['canForceContinue'])
        command(room, s, 'confirm_encounter', encounter=encounter['id'])
        public = game.public_state(room, 10000)
        self.assertEqual((public['pendingCount'], public['readyCount']), (1, 1))
        self.assertTrue(public['canForceContinue'])
        game.continue_decision(room, force=True)
        self.assertEqual(r['waitUntil'], room['tick'] + 2)

    def test_confirmation_is_authorized_versioned_idempotent_and_survives_snapshot(self):
        room, r, m, y, extras = prepared()
        s = extras[0]
        encounter = self.loan(room, r, s)
        game.open_decision(room, [], encounter=encounter)
        for actor, event in ((m, encounter['id']), (r, -1), (r, True)):
            with self.assertRaises(ValueError):
                command(room, actor, 'confirm_encounter', encounter=event)
        packet = dict(
            kind='confirm_encounter',
            encounter=encounter['id'],
            seq=r['lastSeq'] + 1,
            version=r['actionVersion'],
            batch=room['decision']['id'],
        )
        stale = dict(packet, version=-1)
        with self.assertRaises(game.StaleAction):
            game.action(room, r, stale)
        game.action(room, r, packet)
        game.action(room, r, packet)
        command(room, r, 'confirm_encounter', encounter=encounter['id'])
        self.assertEqual(encounter['confirmed'], [r['id']])
        self.assertEqual(len([e for e in room['events'] if e['type'] == 'encounter_confirm']), 1)
        restored = json.loads(json.dumps(room))
        self.assertEqual(restored['decision'], room['decision'])
        restored_s = next(p for p in restored['players'] if p['id'] == s['id'])
        self.assertFalse(
            game.private_state(restored, restored_s, 10000)['me']['encounter']['confirmed']
        )
        restored['technical'] = dict(resumeAt=None)
        with self.assertRaises(ValueError):
            command(restored, restored_s, 'confirm_encounter', encounter=encounter['id'])
        self.assertIsNone(restored['decision']['remaining'])
        restored['technical'] = None
        command(restored, restored_s, 'confirm_encounter', encounter=encounter['id'])
        self.assertEqual(restored['decision']['remaining'], game.AUTO_CONTINUE_MS)

    def test_success_and_failure_pause_expose_same_public_confirmation_metadata(self):
        original, r, m, y, extras = prepared()
        original['players'][original['players'].index(r)]['station'] = extras[0]['station']
        projections = []
        observers = []
        for draw in (0, 0.9):
            room = copy.deepcopy(original)
            with patch.object(game, 'random_value', return_value=draw):
                encounter = game.resolve_hat(room, [])
            game.open_decision(room, [], encounter=encounter)
            state = game.public_state(room, 10000)
            projections.append(
                {k: state[k] for k in ('phase', 'pendingCount', 'readyCount', 'canForceContinue')}
            )
            for forbidden in (
                'encounter',
                'participants',
                'target',
                'changed',
                'confirmed',
                'hatContacts',
                'sumireko',
            ):
                self.assertNotIn(forbidden, state)
            observer = game.private_state(room, game.by_role(room)['yukari'], 10000)
            observer.pop('markers')  # Co-located brown markers carry one shared visible hat.
            observers.append(observer)
        self.assertEqual(*projections)
        self.assertEqual(*observers)  # Includes private batch IDs: no event-counter side channel.

    def test_contact_precedes_new_loan_and_same_tick_victory(self):
        for with_yukari in (False, True):
            room, r, m, y, extras = prepared()
            s = extras[0]
            m['station'] = s['station']
            if with_yukari:
                y['station'] = s['station']
            travel(room, r, 's059', 's060', remaining=1)
            with patch.object(game, 'random_value', return_value=0):
                game.rail_step(room)
            self.assertEqual(room['result']['outcome'], 'hifuu')
            result = next(x for x in room['result']['individuals'] if x['player'] == s['id'])
            self.assertTrue(result['won'])  # Other uncaught Sumirekos preserve a group win.
            self.assertEqual(result['eligible'], not with_yukari)
            self.assertEqual(room['hat']['usesLeft'], 2)
            self.assertFalse(s['hatEligible'])
            self.assertIsNone(room['decision'])

    def test_maribel_only_receives_last_stop_even_after_corruption(self):
        room, r, m, y, extras = prepared()
        r['lastArrival'] = dict(station='s059', tick=4)
        travel(room, r, 's059', 's060', remaining=2)
        for corrupted in (False, True):
            m['corrupted'] = corrupted
            me = game.private_state(room, m, 10000)['me']
            self.assertEqual(me['renkoLastArrival'], dict(station='s059', tick=4))
            self.assertEqual(set(me['renkoLastArrival']), {'station', 'tick'})
        for p in [r, y] + extras:
            self.assertNotIn('renkoLastArrival', game.private_state(room, p, 10000)['me'])
        r['trip']['remaining'] = 1
        game.rail_step(room)
        self.assertEqual(
            game.private_state(room, m, 10000)['me']['renkoLastArrival'],
            dict(station='s060', tick=1),
        )

    def test_anonymous_tokens_and_timestamps_reset_without_swapping(self):
        room, r, m, y, extras = prepared()
        s = extras[0]
        before = {r['mapToken'], s['mapToken']}
        room['tick'] = 3
        r['station'] = s['station']
        game.update_sightings(room, [entry(r, s['station'], 11)])
        after = {r['mapToken'], s['mapToken']}
        self.assertFalse(before & after)
        self.assertEqual(r['lastSighting'], s['lastSighting'])
        self.assertEqual(s['lastSighting']['tick'], 3)
        game.update_sightings(room, [])
        self.assertEqual(after, {r['mapToken'], s['mapToken']})
        r['station'] = 's001'
        game.update_sightings(room, [])
        r['station'] = s['station']
        game.update_sightings(room, [])
        self.assertFalse(after & {r['mapToken'], s['mapToken']})

    def test_pending_seats_name_only_publicly_seated_travelers(self):
        room, r, m, y, extras = prepared()
        everyone = [r, m, y, extras[0]]
        game.open_decision(room, everyone)
        public = game.public_state(room, 10000)
        self.assertEqual(public['pendingSeats'], sorted(p['seat'] for p in everyone))
        room['brownAnonymous'] = True
        public = game.public_state(room, 10000)
        self.assertEqual(public['pendingSeats'], sorted([m['seat'], y['seat']]))
        self.assertEqual(public['pendingCount'], 4)
        room['decision']['choices'][str(m['id'])] = dict(kind='wait')
        self.assertEqual(game.public_state(room, 10000)['pendingSeats'], [y['seat']])
        # Encounter confirmations never name anyone, even before anonymity.
        room['brownAnonymous'] = False
        encounter = dict(
            id=99, tick=0, participants=[r['id'], extras[0]['id']],
            target=extras[0]['id'], changed=False, confirmed=[],
        )
        game.open_decision(room, [], encounter=encounter)
        public = game.public_state(room, 10000)
        self.assertEqual((public['pendingCount'], public['pendingSeats']), (2, []))

    def test_brown_letters_rotate_on_meetings_without_collisions(self):
        room, r, m, y, extras = prepared()
        brown = [r] + extras
        labels = lambda: [p['mapLabel'] for p in brown]
        start = labels()
        self.assertEqual(sorted(start), list('ABCD'))
        self.assertNotIn('mapLabel', m)
        room['tick'] = 3
        r['station'] = extras[0]['station']
        game.update_sightings(room, [entry(r, r['station'], 11)])
        met = {r['mapLabel'], extras[0]['mapLabel']}
        self.assertFalse(met & set(start))
        self.assertEqual([p['mapLabel'] for p in extras[1:]], start[2:])
        game.update_sightings(room, [])
        self.assertEqual(met, {r['mapLabel'], extras[0]['mapLabel']})
        # After a full cycle, letters still shown elsewhere are skipped.
        room['mapLabelSerial'] = len(game.MAP_LABELS)
        r['station'] = 's001'
        game.update_sightings(room, [])
        r['station'] = extras[0]['station']
        game.update_sightings(room, [])
        self.assertEqual(len(set(labels())), len(brown))
        self.assertFalse({r['mapLabel'], extras[0]['mapLabel']} & set(start[2:]))
        markers = {mk['label'] for mk in game.public_markers(room) if mk['appearance'] == 'brown'}
        self.assertEqual(markers, set(labels()))

    def test_brown_seats_are_public_until_first_renko_contact_either_outcome(self):
        for draw in (0, 0.9):
            room, r, m, y, extras = prepared()
            brown = {p['id']: p for p in [r] + extras}
            seats = lambda: {
                mk['seat']: mk['lastArrival']['station']
                for mk in game.public_markers(room)
                if mk['appearance'] == 'brown'
            }
            self.assertEqual(seats(), {p['seat']: p['station'] for p in brown.values()})
            # Sumirekos meeting each other only reshuffle letters.
            extras[1]['station'] = extras[0]['station']
            game.update_sightings(room, [])
            self.assertIsNone(game.resolve_hat(room, []))
            self.assertEqual(len(seats()), len(brown))
            before = {mk['id'] for mk in game.public_markers(room)}
            travel(room, r, 's059', extras[0]['station'], remaining=1)
            with patch.object(game, 'random_value', return_value=draw):
                game.rail_step(room)
            self.assertEqual(room['hat']['holder'] == r['id'], bool(draw))
            self.assertTrue(room['brownAnonymous'])
            markers = [mk for mk in game.public_markers(room) if mk['appearance'] == 'brown']
            for marker in markers:
                self.assertEqual(set(marker), {'id', 'appearance', 'lastArrival', 'hat'})
            # Every brown at the contact station gets an id never paired with a seat.
            met = [mk for mk in markers if mk['lastArrival']['station'] == extras[0]['station']]
            self.assertEqual(len(met), 3)
            self.assertFalse({mk['id'] for mk in met} & before)
            # Separating and the hat coming home never bring the seats back.
            room['tick'] = room['hat']['until'] or room['tick']
            game.resolve_hat(room, [])
            r['station'] = 's001'
            game.update_sightings(room, [])
            for marker in game.public_markers(room):
                if marker['appearance'] == 'brown':
                    self.assertEqual(set(marker), {'id', 'appearance', 'lastArrival', 'hat'})

    def test_public_projection_contains_no_real_brown_position_links(self):
        room, r, m, y, extras = prepared()
        room['brownAnonymous'] = True  # After Renko's first Sumireko contact.
        room['arrivals'] = [
            dict(player=p['id'], station=p['station'], tick=2, terminal=True)
            for p in room['players']
        ]
        game.open_decision(room, [r, extras[0]])
        extras[0].update(hatEligible=False, winEligible=False)
        public = game.public_state(room, 10000)
        self.assertEqual(public['pendingCount'], 2)
        self.assertNotIn('pendingPlayers', public)
        for p in public['players']:
            self.assertNotIn('lastArrival', p)
        for marker in public['markers']:
            if marker['appearance'] == 'brown':
                self.assertEqual(set(marker), {'id', 'appearance', 'lastArrival', 'hat'})
                self.assertNotIn(marker['id'], [str(p['id']) for p in room['players']])
        for e in public['arrivals']:
            if e['appearance'] == 'brown':
                self.assertNotIn('player', e)
                self.assertNotIn('terminal', e)
        for p in room['players']:
            text = json.dumps(game.private_state(room, p, 10000))
            for forbidden in (
                'hatEligible',
                'winEligible',
                'mapToken',
                'mapLabel',
                'brownMeetings',
                'sumireko_contact',
            ):
                self.assertNotIn(forbidden, text)

    def test_contacts_do_not_change_action_or_pause_membership(self):
        room, r, m, y, extras = prepared()
        s = extras[0]
        y['station'] = s['station']
        game.resolve_contacts(room)
        game.open_decision(room, [s])
        self.assertIn(s['id'], room['decision']['required'])
        command(room, s, 'wait')
        game.continue_decision(room)
        self.assertEqual(s['waitUntil'], 2)
        for p in room['players']:
            p['seen'] = 16000 if p is not s else 10000
        room['hostSeen'] = 16000
        game.advance(room, 16000)
        self.assertIsNotNone(room['technical'])

    def test_snapshots_preserve_loans_and_secret_results(self):
        room, r, m, y, extras = prepared()
        self.loan(room, r, extras[0])
        snapshot = json.loads(json.dumps(room))
        self.assertEqual(game.public_state(snapshot, 10000), game.public_state(room, 10000))
        y['station'] = extras[0]['station']
        game.resolve_contacts(room)
        game.end(room, 'hifuu', 'fixture')
        public = game.public_state(room, 10000)
        self.assertEqual(sum(x['won'] for x in public['result']['individuals']), 3)
        self.assertEqual(public['result']['sumireko'], dict(won=True, total=3, remaining=2))
        self.assertTrue(any(e['type'] == 'sumireko_contact' for e in public['replay']))
        for outcome in ('yukari', 'draw', 'aborted'):
            room, *_ = prepared()
            game.end(room, outcome, 'fixture')
            self.assertFalse(any(x['won'] for x in room['result']['individuals']))

    def test_sumireko_group_win_depends_on_at_least_one_uncaught_member(self):
        for count in (3, 4, 5, 6):
            for remaining in range(count - 2):
                room, r, m, y, extras = prepared(count)
                for i, p in enumerate(extras):
                    p['winEligible'] = i < remaining
                    p['hatEligible'] = False  # Maribel-sealed members can still preserve victory.
                for p in room['players']:
                    self.assertNotIn(
                        'sumireko', game.private_state(room, p, 10000).get('result') or {}
                    )
                self.assertIsNone(room['result'])
                game.end(room, 'hifuu', 'fixture')
                won = bool(remaining)
                self.assertEqual(
                    room['result']['sumireko'], dict(won=won, total=count - 3, remaining=remaining)
                )
                self.assertTrue(all(item['won'] == won for item in room['result']['individuals']))
                self.assertEqual(
                    sum(item['eligible'] for item in room['result']['individuals']), remaining
                )

    def test_last_uncaught_member_captured_on_victory_tick_loses_group_victory(self):
        room, r, m, y, extras = prepared()
        for s in extras[:-1]:
            s['winEligible'] = False
        last = extras[-1]
        last['station'] = m['station'] = y['station'] = 's060'
        travel(room, r, 's059', 's060', remaining=1)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'], 'hifuu')
        self.assertEqual(room['result']['sumireko'], dict(won=False, total=3, remaining=0))
        self.assertFalse(any(item['won'] for item in room['result']['individuals']))


if __name__ == '__main__':
    unittest.main()
