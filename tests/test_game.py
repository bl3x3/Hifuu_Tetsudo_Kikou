import copy
import json
import unittest
from server import game
from server.data import ADJACENCY, STATIONS, EDGES, LINES, EDGE_LINE, station_lines


def prepared():
    room = game.create_room('TEST1', 10000)
    for name in ('一', '二', '三'):
        p = game.join(room, name, 10000)
        p['ready'] = True
    game.setup(room, 'S04', 10000)
    # Stable roles and separated stations isolate the conditions being tested.
    for p, role, station in zip(room['players'], ('renko', 'maribel', 'yukari'), ('s001', 's055', 's088')):
        p.update(role=role, station=station, lastArrival=dict(station=station, tick=0))
        game.deal(room, p)
    room['phase'], room['decision'] = 'running', None
    return room, *room['players']


def command(room, p, kind, **fields):
    if kind == 'travel' and 'destination' not in fields:
        card = next(c for c in p['hand'] if c['id'] == fields['card'])
        fields['destination'] = next(s for s in reversed(card['nodes']) if s != p['station'])
    game.action(room, p, dict(kind=kind, seq=p['lastSeq']+1, version=p['actionVersion'],
                             batch=room['decision']['id'] if room['decision'] else None, **fields))


def travel(room, p, source, target, remaining=None):
    edge = next(e for e, to in ADJACENCY[source] if to == target)
    p['station'] = source
    card = game.make_card([source, target], [edge], 'fixture')
    p['queued'] = card
    game.depart(room, p)
    if remaining is not None:
        p['trip']['remaining'] = remaining


def multi_stop_trip(room, player, remaining=2):
    nodes=['s059','s060','s063']
    edges=[next(edge for edge,target in ADJACENCY[source] if target==destination)
           for source,destination in zip(nodes,nodes[1:])]
    player['station']=nodes[0]
    player['queued']=game.make_card(nodes,edges,'stop-fixture')
    game.depart(room,player)
    player['trip']['remaining']=remaining


def entry(p, station, uid, stop=True):
    return dict(player=p, station=station, id=uid, stop=stop)


class RulesTest(unittest.TestCase):
    def test_stop_at_next_station_preserves_travel_then_reopens_routes(self):
        room,player,_,_=prepared()
        multi_stop_trip(room,player)
        hand=copy.deepcopy(player['hand'])
        before=game.travel_status(room,player)
        tickets=(player['baseChanges'],player['bonusChanges'])
        packet=dict(kind='stop',card=player['trip']['card']['id'],segment=0,
                    seq=player['lastSeq']+1,version=player['actionVersion'],batch=None)
        game.action(room,player,packet)
        game.action(room,player,packet)
        self.assertEqual(player['trip']['remaining'],2)
        self.assertIsNone(player['station'])
        self.assertEqual(player['hand'],hand)
        self.assertEqual(room['phase'],'running')
        motion=game.travel_status(room,player)
        self.assertTrue(motion['stopRequested'])
        self.assertEqual(motion['destination'],'s060')
        self.assertEqual(motion['remainingRailMinutes'],30)
        self.assertLess(motion['remainingRailMinutes'],before['remainingRailMinutes'])
        self.assertFalse(game.private_state(room,player,10000)['me']['canStop'])
        game.rail_step(room)
        self.assertIsNone(player['station'])
        game.rail_step(room)
        self.assertEqual(player['station'],'s060')
        self.assertIsNone(player['trip'])
        self.assertIsNone(player['departAt'])
        self.assertEqual(room['phase'],'decision')
        self.assertEqual(room['decision']['required'],[player['id']])
        self.assertNotEqual(player['hand'],hand)
        self.assertEqual((player['baseChanges'],player['bonusChanges']),tickets)
        self.assertEqual(len([event for event in room['events'] if event['type']=='stop_requested']),1)
        self.assertTrue(game.private_state(room,player,10000)['me']['availableLines'])
        command(room,player,'travel',card=player['hand'][0]['id'])
        game.continue_decision(room)
        self.assertEqual(room['phase'],'running')
        self.assertIsNotNone(player['trip'])
        self.assertFalse(player['trip'].get('stopRequested',False))

    def test_stop_request_is_private_and_allowed_during_other_decisions(self):
        room,player,other,_=prepared()
        multi_stop_trip(room,player)
        game.open_decision(room,[other])
        choices=copy.deepcopy(room['decision']['choices'])
        self.assertTrue(game.private_state(room,player,10000)['me']['canStop'])
        command(room,player,'stop',card=player['trip']['card']['id'],segment=0)
        self.assertEqual(room['decision']['choices'],choices)
        self.assertEqual(room['tick'],0)
        self.assertTrue(game.private_state(room,player,10000)['me']['motion']['stopRequested'])
        self.assertNotIn('stopRequested',json.dumps(game.public_state(room,10000)))
        self.assertNotIn('stopRequested',json.dumps(game.private_state(room,other,10000)))

    def test_stop_request_during_hold_and_layover_waits_for_next_arrival(self):
        for mode in ('hold','layover'):
            with self.subTest(mode=mode):
                room,player,_,_=prepared()
                multi_stop_trip(room,player,remaining=1)
                if mode=='hold':
                    player['holdUntil']=2
                else:
                    game.rail_step(room)
                    player['trip']['remaining']=1
                    self.assertEqual(player['station'],'s060')
                target=player['trip']['card']['nodes'][player['trip']['segment']+1]
                command(room,player,'stop',card=player['trip']['card']['id'],segment=player['trip']['segment'])
                self.assertIsNone(room['decision'])
                for _ in range(2 if mode=='hold' else 1):
                    game.rail_step(room)
                    self.assertIsNotNone(player['trip'])
                game.rail_step(room)
                self.assertEqual(player['station'],target)
                self.assertIsNone(player['trip'])
                self.assertEqual(room['phase'],'decision')

    def test_stop_rejects_stale_segment_and_station_actions(self):
        room,player,_,_=prepared()
        with self.assertRaises(ValueError):
            command(room,player,'stop',card='missing',segment=0)
        multi_stop_trip(room,player,remaining=1)
        old_card=player['trip']['card']['id']
        game.rail_step(room)
        with self.assertRaises(game.StaleAction):
            command(room,player,'stop',card=old_card,segment=0)
        self.assertFalse(player['trip'].get('stopRequested',False))
        room['technical']=dict(resumeAt=None)
        self.assertFalse(game.private_state(room,player,10000)['me']['canStop'])
        with self.assertRaises(ValueError):
            command(room,player,'stop',card=old_card,segment=1)

    def test_next_station_stop_keeps_meeting_priority(self):
        room,player,maribel,_=prepared()
        multi_stop_trip(room,player,remaining=1)
        maribel['station']='s060'
        command(room,player,'stop',card=player['trip']['card']['id'],segment=0)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'],'hifuu')
        self.assertIsNone(room['decision'])

    def test_short_branch_returns_to_hub_with_a_different_line(self):
        room,p,_,_=prepared()
        # Every short branch with a junction must offer an exit on return,
        # regardless of seed and even with no change tickets remaining.
        for line in LINES:
            if len(line['nodes'])!=2: continue
            for hub in line['nodes']:
                if len(station_lines(hub))<2: continue
                for seed in range(30):
                    room['seed']=str(seed)
                    p.update(station=hub,baseChanges=0,bonusChanges=0,
                             hand=[game.line_offer(line,hub,'return')])
                    game.deal(room,p)
                    self.assertNotEqual(p['hand'][0]['lineId'],line['id'])
        for node in STATIONS:
            sid=node['station_id'] if 'station_id' in node else node['id']
            lines=station_lines(sid)
            if len(lines)==1:
                p.update(station=sid,hand=[game.line_offer(lines[0],sid,'only')])
                game.deal(room,p)
                self.assertEqual(p['hand'][0]['lineId'],lines[0]['id'])

    def test_submitted_choice_is_locked_and_only_pending_seats_are_public(self):
        room,r,m,y=prepared()
        game.open_decision(room,[m])
        command(room,r,'travel',card=r['hand'][0]['id'])
        before=copy.deepcopy(room['decision']['choices'])
        private=game.private_state(room,r,10000)
        self.assertTrue(private['me']['submitted'])
        self.assertFalse(private['me']['canAct'])
        self.assertFalse(private['me']['canChange'])
        self.assertEqual(private['pendingPlayers'],[m['id']])
        self.assertNotIn('chosenDestination',game.public_state(room,10000))
        with self.assertRaises(ValueError):command(room,r,'wait')
        self.assertEqual(room['decision']['choices'],before)
        command(room,m,'wait')
        game.continue_decision(room)
        self.assertEqual(room['phase'],'running')
        self.assertIsNotNone(r['trip'])

    def test_request_wait_accept_locks_target_and_requires_host_continue(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'request_wait',target=m['id'])
        self.assertTrue(r['requestUsed'])
        self.assertEqual(room['decision']['request']['status'],'pending')
        public=game.public_state(room,10000)
        self.assertEqual(public['request']['requesterSeat'],r['seat'])
        self.assertEqual(public['request']['targetSeat'],m['seat'])
        command(room,m,'respond_request',accept=True)
        self.assertEqual(room['decision']['choices'][str(m['id'])]['kind'],'wait')
        self.assertEqual(room['phase'],'decision')
        command(room,r,'wait')
        game.continue_decision(room)
        self.assertEqual(room['phase'],'running')
        self.assertEqual(m['waitUntil'],2)

    def test_request_wait_decline_keeps_target_choice_open(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'request_wait',target=m['id'])
        command(room,m,'respond_request',accept=False)
        self.assertNotIn(str(m['id']),room['decision']['choices'])
        command(room,m,'travel',card=m['hand'][0]['id'])
        self.assertEqual(room['decision']['choices'][str(m['id'])]['kind'],'travel')
        self.assertEqual(game.public_state(room,10000)['request']['status'],'declined')

    def test_request_limits_one_per_batch_and_per_player(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'request_wait',target=m['id'])
        with self.assertRaises(ValueError):
            command(room,m,'request_wait',target=r['id'])
        command(room,m,'respond_request',accept=False)
        command(room,r,'wait')
        command(room,m,'wait')
        game.continue_decision(room)
        game.open_decision(room,[r,m])
        with self.assertRaises(ValueError):
            command(room,r,'request_wait',target=m['id'])

    def test_request_projection_does_not_expose_private_route_or_identity(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'request_wait',target=m['id'])
        text=json.dumps(game.public_state(room,10000),ensure_ascii=False)
        for forbidden in (r['hand'][0]['id'],m['hand'][0]['id'],'maribel','yukari','requestUsed'):
            self.assertNotIn(forbidden,text)

    def test_rejoining_reuses_seat_without_reusing_player_identity(self):
        room = game.create_room('SEATS', 10000)
        for name in ('A', 'B', 'C'):
            game.join(room, name, 10000)
        room['players'].pop(1)
        p = game.join(room, 'D', 10000)
        self.assertEqual((p['id'], p['seat']), (4, 2))
        self.assertEqual(sorted(p['seat'] for p in game.public_state(room, 10000)['players']), [1,2,3])

    def test_initial_wait_is_unlimited_and_host_can_force_departure(self):
        room, r, m, y = prepared()
        room['phase'] = 'briefing'
        game.open_decision(room, room['players'], initial=True)
        command(room, r, 'wait')
        game.advance(room, 400000)
        public = game.public_state(room, 400000)
        self.assertEqual((public['readyCount'], public['autoWaitCount']), (1, 0))
        self.assertIsNone(public['decisionRemaining'])
        self.assertTrue(game.private_state(room, m, 400000)['me']['canAct'])
        self.assertEqual(room['tick'], 0)
        for p in room['players']: p['seen'] = 400000
        with self.assertRaises(ValueError): game.launch(room, 400000)
        game.launch(room, 400000, force=True)
        self.assertEqual(room['phase'], 'running')
        self.assertTrue(all(p['waitUntil'] == 2 for p in room['players']))

    def test_repeated_recovered_outages_do_not_accumulate(self):
        room, r, m, y = prepared()
        now = 10000
        for _ in range(3):
            now += 18000
            game.advance(room, now)
            self.assertIsNone(room['result'])
            for delta in (100, 3000):
                now += delta
                room['hostSeen'] = now
                for p in room['players']: p['seen'] = now
                game.advance(room, now)
            self.assertIsNone(room['technical'])
            self.assertEqual(room['technicalUsed'], 0)
        self.assertEqual(room['tick'], 0)

    def test_graph_and_all_station_ticket_generation(self):
        self.assertEqual((len(STATIONS), len(EDGES)), (100, 132))
        room, p, _, _ = prepared()
        reached, stack = set(), ['s001']
        while stack:
            s = stack.pop()
            if s in reached: continue
            reached.add(s)
            stack.extend(to for _, to in ADJACENCY[s])
        self.assertEqual(len(reached), 100)
        for station in STATIONS:
            p['station'] = station['id']
            game.deal(room, p)
            self.assertEqual(len(p['hand']), 1)
            for c in p['hand']:
                self.assertEqual(len(c['nodes']), len(set(c['nodes'])))
                self.assertEqual(c['nodes'], next(l for l in LINES if l['id']==c['lineId'])['nodes'])
                self.assertIn(p['station'], c['nodes'])
                self.assertEqual(c['ticks'], sum(c['segments'])+len(c['nodes'])-2)
                if 'e131' in c['edges']:
                    self.assertEqual(c['ticks'], 4)
                    self.assertEqual(len(c['nodes']), 2)
            if station['id'] in ('s023', 's060'):
                self.assertTrue(any('e131' in l['edges'] for l in station_lines(station['id'])))

    def test_ordinary_pair_win(self):
        room, r, m, y = prepared()
        r['station'] = m['station']
        game.terminal_meeting(room)
        self.assertEqual(room['result']['outcome'], 'hifuu')

    def test_merged_hokkaido_lines_offer_continuous_travel_and_keep_stops(self):
        self.assertEqual(len(LINES),35)
        self.assertEqual(sum(len(l['nodes'])==2 for l in LINES),5)
        self.assertEqual(len({EDGE_LINE[f'e{i:03}']['id'] for i in range(1,11)}),2)
        room,r,m,y=prepared()
        line=EDGE_LINE['e005']
        r['hand']=[game.line_offer(line,'s001','hokkaido')]
        ticket=game.select_destination(r['hand'][0],'s001','s007')
        self.assertEqual(ticket['nodes'],['s001','s006','s007'])
        self.assertEqual(ticket['ticks'],22)  # 90 + 225 minutes, plus 15-minute intermediate stop.
        r['queued']=ticket;game.depart(room,r)
        for _ in range(6):game.rail_step(room)
        self.assertEqual(r['station'],'s006')
        self.assertIsNone(room['decision'])
        self.assertFalse(game.can_act(room,r))
        game.rail_step(room)
        self.assertIsNone(r['station'])
        self.assertEqual(r['trip']['card']['nodes'][-1],'s007')

    def test_akita_niigata_connection_is_selectable_both_ways(self):
        line=EDGE_LINE['e132']
        self.assertEqual(line['nodes'],['s012','s011','s013','s017','s016','s041'])
        for start,end in (('s016','s041'),('s041','s016')):
            self.assertIn(line,station_lines(start))
            ticket=game.select_destination(game.line_offer(line,start,'japan-sea'),start,end)
            self.assertEqual(ticket['nodes'],[start,end])
            self.assertEqual(ticket['edges'],['e132'])
            self.assertEqual(ticket['ticks']*15,240)

    def test_every_drawn_line_station_is_selectable_in_both_directions(self):
        for line in LINES:
            origin=line['nodes'][len(line['nodes'])//2]
            offer=game.line_offer(line,origin,'all-stops')
            for destination in line['nodes']:
                if destination==origin:continue
                ticket=game.select_destination(offer,origin,destination)
                self.assertEqual(ticket['nodes'][0],origin)
                self.assertEqual(ticket['nodes'][-1],destination)
                self.assertEqual(ticket['ticks'],sum(ticket['segments'])+len(ticket['nodes'])-2)
                self.assertEqual(ticket['color'],line['color'])
                self.assertEqual(ticket['edges'],offer['edges'][min(offer['nodes'].index(origin),offer['nodes'].index(destination)):max(offer['nodes'].index(origin),offer['nodes'].index(destination))][::1 if offer['nodes'].index(destination)>offer['nodes'].index(origin) else -1])

    def test_long_line_is_not_truncated_and_intermediate_destination_locks(self):
        room,r,m,y=prepared()
        line=max(LINES,key=lambda l:len(l['nodes']))
        self.assertGreater(len(line['nodes']),7)
        r['station']=line['nodes'][0]
        r['hand']=[game.line_offer(line,r['station'],'long')]
        destination=line['nodes'][2]
        command(room,r,'travel',card=r['hand'][0]['id'],destination=destination)
        self.assertEqual(r['queued']['nodes'],line['nodes'][:3])
        self.assertEqual(r['queued']['ticks'],sum(r['queued']['segments'])+1)
        with self.assertRaises(ValueError):command(room,r,'travel',card=r['hand'][0]['id'],destination=line['nodes'][-1])

    def test_off_line_and_current_station_destinations_rejected(self):
        room,r,m,y=prepared()
        for destination in (r['station'],'s999',None):
            with self.assertRaises(ValueError):
                command(room,r,'travel',card=r['hand'][0]['id'],destination=destination)

    def test_arrival_predictions_include_hold_departure_and_intermediate_dwell(self):
        room,r,m,y=prepared()
        line=max(LINES,key=lambda l:len(l['nodes']))
        r['station']=line['nodes'][2]
        r['hand']=[game.line_offer(line,r['station'],'prediction')]
        room['tick']=10;r['holdUntil']=12
        state=game.private_state(room,r,10000)
        for item in state['me']['hand'][0]['destinations']:
            if item['current']:continue
            ticket=game.select_destination(r['hand'][0],r['station'],item['station'])
            self.assertEqual(item['arrivalTick'],12+ticket['ticks'])
        self.assertEqual(len(state['me']['hand'][0]['destinations']),len(line['nodes']))

    def test_private_live_position_countdown_freezes_during_decision(self):
        room,r,m,y=prepared()
        travel(room,m,'s063','s060')
        room['railAccum']=1000
        motion=game.private_state(room,m,10000)['me']['motion']
        self.assertAlmostEqual(motion['progress'],1/6)
        self.assertEqual(motion['remainingRailMinutes'],37.5)
        game.open_decision(room,[y])
        room['hostSeen']=11000
        for p in room['players']:p['seen']=11000
        game.advance(room,11000)
        new=game.private_state(room,m,11000)['me']
        self.assertTrue(new['otherPlayersDeciding'])
        self.assertEqual(new['motion']['progress'],motion['progress'])
        self.assertEqual(new['motion']['remainingRailMinutes'],motion['remainingRailMinutes'])
        self.assertTrue(new['motion']['paused'])
        self.assertNotIn('motion',json.dumps(game.public_state(room,11000)))
        self.assertIsNone(game.private_state(room,r,11000)['me']['motion'])

    def test_line_colors_unique_and_same_on_public_map(self):
        from server.data import live_map
        self.assertEqual(len({l['color'] for l in LINES}),len(LINES))
        svg=live_map()
        for line in LINES:
            self.assertIn(f'stroke="{line["color"]}"',svg)
        tokaido=EDGE_LINE['e068']
        self.assertEqual(tokaido['nodes'][0],'s023')
        self.assertEqual(tokaido['nodes'][-1],'s061')
        self.assertEqual(EDGE_LINE['e032']['id'],tokaido['id'])
        self.assertEqual(EDGE_LINE['e035']['id'],tokaido['id'])

    def test_triple_priority_uses_prior_corruption(self):
        for corrupted, result in [(False, 'hifuu'), (True, 'yukari')]:
            room, r, m, y = prepared()
            r['station'] = y['station'] = m['station']
            m['corrupted'] = corrupted
            game.terminal_meeting(room)
            self.assertEqual(room['result']['outcome'], result)

    def test_secret_corruption_continues_and_invalidates_bonus(self):
        room, r, m, y = prepared()
        y['station'] = m['station']
        m['bonusChanges'] = 1
        game.resolve_events(room, [])
        self.assertTrue(m['corrupted'])
        self.assertIsNone(room['result'])
        self.assertEqual(m['bonusChanges'], 0)
        self.assertEqual(r['messages'], [])
        count = len(m['messages'])
        game.resolve_events(room, [])
        self.assertEqual(len(m['messages']), count)

    def test_kyoto_protection_before_remote_corruption(self):
        room, r, m, y = prepared()
        room['tick'] = 8
        y['station'] = m['station']
        game.resolve_events(room, [entry(r, 's060', 1)], lambda _: 0)
        self.assertFalse(m['corrupted'])
        self.assertEqual(m['protectionUntil'], 56)
        self.assertEqual(r['messages'], [])
        self.assertEqual(y['messages'], [])
        room['tick'] = 55
        game.resolve_events(room, [])
        self.assertFalse(m['corrupted'])
        room['tick'] = 56
        game.resolve_events(room, [])
        self.assertTrue(m['corrupted'])

    def test_kyoto_each_branch_failure_consumed_and_no_retry(self):
        room, r, m, y = prepared()
        room['tick'] = 8
        game.resolve_events(room, [entry(r, 's060', 1), entry(m, 's060', 2)], lambda _: .8)
        game.resolve_events(room, [entry(r, 's060', 3), entry(m, 's060', 4)], lambda _: 0)
        self.assertEqual(m['protectionUntil'], 0)
        self.assertEqual(r['messages'], [])
        self.assertEqual(len([e for e in room['events'] if e['type']=='roll']), 2)

    def test_kyoto_window_and_nonstop_exclusion(self):
        for tick, stop in [(7, True), (56, True), (8, False)]:
            room, r, m, y = prepared()
            room['tick'] = tick
            game.resolve_events(room, [entry(r, 's060', 1, stop)], lambda _: 0)
            self.assertEqual(room['attempts'], {})

    def test_kyoto_no_cure_and_dialogue_no_sender(self):
        room, r, m, y = prepared()
        room['tick'], m['corrupted'] = 9, True
        game.resolve_events(room, [entry(r, 's060', 1), entry(m, 's060', 2)], lambda _: 0)
        self.assertTrue(m['corrupted'])
        self.assertEqual(m['messages'], [])
        self.assertEqual(r['messages'][0]['text'], '看，门的这里。门的另一边。显然是现世对吧？')
        self.assertEqual(set(r['messages'][0]), {'id','tick','text'})

    def test_nagano_remote_hold_exactly_two_intervals(self):
        room, r, m, y = prepared()
        travel(room, m, 's059', 's060', remaining=2)
        game.resolve_events(room, [entry(y, 's039', 1, False)], lambda _: 0)
        self.assertEqual(m['holdUntil'], 2)
        for _ in range(2): game.rail_step(room)
        self.assertEqual(m['trip']['remaining'], 2)
        game.rail_step(room)
        self.assertEqual(m['trip']['remaining'], 1)
        game.rail_step(room)
        self.assertEqual(m['station'], 's060')
        self.assertEqual(y['messages'], [])

    def test_nagano_revisits_refresh_and_duplicate_id_dedup(self):
        room, r, m, y = prepared()
        a, b = entry(m,'s039',1), entry(y,'s039',2)
        game.resolve_events(room,[a,b],lambda _:0)
        self.assertEqual(m['holdUntil'],2)
        game.resolve_events(room,[a,b],lambda _:0)
        self.assertEqual(len(m['messages']),2)
        room['tick']=1
        game.resolve_events(room,[entry(y,'s039',3)],lambda _:0)
        self.assertEqual(m['holdUntil'],3)

    def test_nara_private_once_and_corrupted_excluded(self):
        room, r, m, y = prepared()
        for p in (r,m,y): game.resolve_events(room,[entry(p,'s063',p['id'])])
        self.assertEqual([p['bonusChanges'] for p in (r,m,y)],[1,1,0])
        game.resolve_events(room,[entry(r,'s063',9)])
        self.assertEqual(r['bonusChanges'],1)
        m['naraUsed'],m['bonusChanges'],m['corrupted']=False,0,True
        game.resolve_events(room,[entry(m,'s063',10)])
        self.assertEqual(m['bonusChanges'],0)

    def test_decision_pauses_game_clock_and_timeout_waits(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r])
        for delta in range(1000,31000,1000):
            now=10000+delta
            room['hostSeen']=now
            for p in room['players']:p['seen']=now
            game.advance(room,now)
        self.assertEqual(room['tick'],0)
        self.assertNotIn('matchRemaining',game.public_state(room,now))
        self.assertEqual(room['phase'],'decision')
        self.assertEqual(r['waitUntil'],0)
        self.assertIsNone(r['trip'])
        game.continue_decision(room, force=True)
        self.assertEqual(room['phase'],'running')
        self.assertEqual(r['waitUntil'],2)

    def test_all_confirm_resume_and_duplicate_is_idempotent(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'wait')
        self.assertEqual(room['phase'],'decision')
        packet=dict(kind='wait',seq=m['lastSeq']+1,version=m['actionVersion'],batch=room['decision']['id'])
        game.action(room,m,packet)
        self.assertEqual(room['phase'],'decision')
        game.continue_decision(room)
        self.assertEqual(room['phase'],'running')
        version=m['actionVersion']
        game.action(room,m,packet)
        self.assertEqual(m['actionVersion'],version)

    def test_wait_end_redraws_and_pauses_again(self):
        for station_id in ('s001','s005'):
            with self.subTest(station=station_id):
                room,player,_,_=prepared()
                player['station']=station_id
                game.deal(room,player)
                hand=copy.deepcopy(player['hand'])
                tickets=(player['baseChanges'],player['bonusChanges'])
                command(room,player,'wait')
                version=player['actionVersion']
                stale=dict(kind='travel',card=hand[0]['id'],destination=hand[0]['nodes'][-1],
                           seq=player['lastSeq']+1,version=version,batch=None)
                self.assertFalse(game.can_act(room,player))
                self.assertEqual(player['hand'],hand)
                game.rail_step(room)
                self.assertEqual(player['hand'],hand)
                self.assertFalse(game.can_act(room,player))
                game.rail_step(room)
                self.assertTrue(game.can_act(room,player))
                self.assertEqual(room['phase'],'decision')
                self.assertEqual(room['decision']['required'],[player['id']])
                self.assertNotEqual(player['hand'][0]['id'],hand[0]['id'])
                if len(station_lines(station_id))>1:
                    self.assertNotEqual(player['hand'][0]['lineId'],hand[0]['lineId'])
                else:
                    self.assertEqual(player['hand'][0]['lineId'],hand[0]['lineId'])
                self.assertEqual(player['actionVersion'],version+1)
                self.assertEqual((player['baseChanges'],player['bonusChanges']),tickets)
                with self.assertRaises(game.StaleAction):
                    game.action(room,player,stale)
                refreshed=copy.deepcopy(player['hand'])
                now=room['updated']+1000
                room['hostSeen']=now
                for p in room['players']:p['seen']=now
                game.advance(room,now)
                self.assertEqual(room['tick'],2)
                self.assertEqual(player['hand'],refreshed)
                command(room,player,'wait')
                game.continue_decision(room)
                game.rail_step(room)
                game.rail_step(room)
                self.assertEqual(room['decision']['required'],[player['id']])
                self.assertNotEqual(player['hand'][0]['id'],refreshed[0]['id'])

    def test_automatic_wait_redraws_after_rail_time_resumes(self):
        room,player,_,_=prepared()
        hand=copy.deepcopy(player['hand'])
        game.open_decision(room,[player])
        now=40000
        room['hostSeen']=now
        for traveler in room['players']:traveler['seen']=now
        game.advance(room,now)
        self.assertEqual(room['tick'],0)
        self.assertEqual(player['hand'],hand)
        self.assertEqual(player['waitUntil'],0)
        self.assertEqual(room['phase'],'decision')
        game.continue_decision(room, force=True)
        self.assertEqual(player['waitUntil'],2)
        for now in (42000,44000):
            room['hostSeen']=now
            for traveler in room['players']:traveler['seen']=now
            game.advance(room,now)
        self.assertEqual(room['tick'],2)
        self.assertNotEqual(player['hand'][0]['id'],hand[0]['id'])

    def test_scheduled_departure_before_arrival_is_missed_meeting(self):
        room,r,m,y=prepared()
        r['station']='s060'
        game.deal(room,r)
        command(room,r,'travel',card=r['hand'][0]['id'])
        travel(room,m,'s059','s060',remaining=1)
        game.rail_step(room)
        self.assertIsNone(r['station'])
        self.assertEqual(m['station'],'s060')
        self.assertIsNone(room['result'])

    def test_same_boundary_arrivals_win(self):
        room,r,m,y=prepared()
        travel(room,r,'s059','s060',remaining=1)
        travel(room,m,'s063','s060',remaining=1)
        room['hostSeen']=12000
        for p in room['players']:p['seen']=12000
        game.advance(room,12000)
        self.assertEqual(room['result']['outcome'],'hifuu')

    def test_no_real_time_limit_even_with_legacy_countdown(self):
        room,r,m,y=prepared()
        room['matchRemaining']=1000  # Old snapshots must not re-enable a deadline.
        room['tickMs']=4000
        for now in range(11000,611000,1000):
            room['hostSeen']=now
            for p in room['players']:p['seen']=now
            game.advance(room,now)
        self.assertEqual(room['tick'],150)
        self.assertIsNone(room['result'])
        self.assertEqual(room['phase'],'running')
        self.assertNotIn('matchRemaining',game.public_state(room,now))
        # A normal meeting can still end this already ten-minute-old game.
        travel(room,r,'s059','s060',remaining=1)
        travel(room,m,'s063','s060',remaining=1)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'],'hifuu')

    def test_journey_timeout_only_after_48_game_hours(self):
        room,renko,maribel,yukari=prepared()
        room['tick']=191
        for now in (12000,14000):
            room['hostSeen']=now
            for player in room['players']:player['seen']=now
            game.advance(room,now)
            if now==12000:
                self.assertEqual(room['tick'],192)
                self.assertIsNone(room['result'])
                self.assertEqual(room['phase'],'running')
        self.assertEqual(room['result']['outcome'],'draw')
        self.assertEqual(room['result']['tick'],193)
        self.assertEqual(room['phase'],'ended')
        self.assertIsNone(room['decision'])
        game.rail_step(room)
        game.advance(room,16000)
        self.assertEqual(room['tick'],193)
        self.assertEqual(len([event for event in room['events'] if event['type']=='end']),1)

    def test_meeting_at_48_hours_still_wins(self):
        room,renko,maribel,yukari=prepared()
        room['tick']=191
        travel(room,renko,'s059','s060',remaining=1)
        travel(room,maribel,'s063','s060',remaining=1)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'],'hifuu')
        self.assertEqual(room['result']['tick'],192)

    def test_timeout_precedes_meeting_after_48_hours(self):
        room,renko,maribel,yukari=prepared()
        room['tick']=192
        travel(room,renko,'s059','s060',remaining=1)
        travel(room,maribel,'s063','s060',remaining=1)
        game.rail_step(room)
        self.assertEqual(room['result']['outcome'],'draw')
        self.assertIsNone(room['decision'])

    def test_decision_and_technical_pauses_do_not_timeout_journey(self):
        for pause in ('decision','technical'):
            with self.subTest(pause=pause):
                room,renko,maribel,yukari=prepared()
                room['tick']=192
                if pause=='decision':
                    game.open_decision(room,[renko])
                else:
                    room['technical']=dict(resumeAt=None)
                for now in (11000,12000):
                    room['hostSeen']=now
                    for player in room['players']:player['seen']=now
                    game.advance(room,now)
                self.assertEqual(room['tick'],192)
                self.assertIsNone(room['result'])

    def test_game_minutes_use_fractional_rail_progress_and_pause(self):
        room,r,m,y=prepared()
        room['tick']=8;room['railAccum']=1000
        self.assertEqual(game.public_state(room,10000)['railMinutes'],127.5)
        game.open_decision(room,[r])
        for now in (11000,12000):
            room['hostSeen']=now
            for p in room['players']:p['seen']=now
            game.advance(room,now)
        self.assertEqual(game.public_state(room,12000)['railMinutes'],127.5)

    def test_intermediate_stop_dwell_and_locked_route(self):
        room,r,m,y=prepared()
        nodes=['s059','s060','s063']
        edges=[next(e for e,to in ADJACENCY[a] if to==b) for a,b in zip(nodes,nodes[1:])]
        r['station']='s059';r['queued']=game.make_card(nodes,edges,'middle')
        game.depart(room,r);r['trip']['remaining']=1
        game.rail_step(room)
        self.assertEqual(r['station'],'s060')
        self.assertEqual(room['phase'],'running')
        self.assertFalse(game.can_act(room,r))
        remaining=r['trip']['remaining']
        game.rail_step(room)
        self.assertIsNone(r['station'])
        self.assertEqual(r['trip']['remaining'],remaining)

    def test_hold_terminal_choice_queued_until_release(self):
        room,r,m,y=prepared()
        m['holdUntil']=2
        game.open_decision(room,[m])
        command(room,m,'travel',card=m['hand'][0]['id'])
        self.assertIsNone(m['queued'])
        game.continue_decision(room)
        self.assertIsNotNone(m['queued'])
        self.assertIsNone(m['trip'])
        game.rail_step(room)
        self.assertIsNotNone(m['station'])
        game.rail_step(room)
        self.assertIsNone(m['station'])
        self.assertIsNone(room['decision'])

    def test_bonus_change_consumed_first_and_no_midroute_changes(self):
        room,r,m,y=prepared()
        r['station']='s005'  # South Chitose remains a junction after the Hokkaido merge.
        game.deal(room,r)
        r['bonusChanges']=1
        edge=next(e for e,_ in ADJACENCY[r['station']] if EDGE_LINE[e['id']]['id'] != r['hand'][0]['lineId'])
        command(room,r,'change',slot=0,edge=edge['id'])
        self.assertEqual((r['baseChanges'],r['bonusChanges']),(1,0))
        command(room,r,'travel',card=r['hand'][0]['id'])
        with self.assertRaises(ValueError):command(room,r,'change',slot=0,edge=edge['id'])

    def test_technical_pause_freezes_game_time_and_aborts(self):
        room,r,m,y=prepared()
        game.advance(room,15100)
        self.assertIsNotNone(room['technical'])
        self.assertEqual(room['railAccum'],0)
        self.assertEqual(room['tick'],0)
        game.advance(room,40100)
        self.assertIsNone(room['result'])
        game.advance(room,99999)
        self.assertEqual(game.public_state(room,99999)['technical']['remaining'],1)
        game.advance(room,100000)
        self.assertEqual(room['result']['outcome'],'aborted')

    def advance_connected(self, room, milliseconds):
        now=room['updated']+milliseconds
        room['hostSeen']=now
        for p in room['players']:p['seen']=now
        game.advance(room,now)

    def test_all_required_confirm_then_three_seconds_auto_continue(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r,m])
        command(room,r,'wait')
        self.advance_connected(room,60000)
        self.assertEqual(room['tick'],0)
        self.assertIsNone(room['decision']['remaining'])
        command(room,m,'wait')
        self.assertEqual(room['decision']['remaining'],3000)
        self.advance_connected(room,2999)
        self.assertEqual(room['phase'],'decision')
        self.assertEqual(room['tick'],0)
        self.advance_connected(room,1)
        self.assertEqual(room['phase'],'running')
        self.assertEqual(room['tick'],0)

    def test_host_pause_before_or_during_countdown(self):
        for before in (True,False):
            with self.subTest(before=before):
                room,r,m,y=prepared()
                game.open_decision(room,[r])
                if before: game.pause_decision(room,True)
                command(room,r,'wait')
                if not before:
                    self.advance_connected(room,2000)
                    game.pause_decision(room,True)
                self.advance_connected(room,60000)
                self.assertTrue(game.public_state(room,room['updated'])['decisionPaused'])
                self.assertEqual(room['tick'],0)
                game.pause_decision(room,False)
                self.assertEqual(room['decision']['remaining'],3000)
                game.continue_decision(room)
                self.assertEqual(room['phase'],'running')

    def test_accepted_request_starts_auto_countdown(self):
        room,r,m,y=prepared()
        game.open_decision(room,[m])
        command(room,r,'request_wait',target=m['id'])
        command(room,m,'respond_request',accept=True)
        self.assertEqual(room['decision']['remaining'],3000)
        self.advance_connected(room,3000)
        self.assertEqual(room['phase'],'running')

    def test_connection_loss_freezes_auto_countdown(self):
        room,r,m,y=prepared()
        game.open_decision(room,[r])
        command(room,r,'wait')
        self.advance_connected(room,1000)
        game.advance(room,room['updated']+6000)
        self.assertEqual(room['decision']['remaining'],2000)
        with self.assertRaises(ValueError): game.continue_decision(room)
        self.advance_connected(room,1000)
        self.advance_connected(room,3000)
        self.assertIsNone(room['technical'])
        self.assertEqual(room['decision']['remaining'],2000)
        self.advance_connected(room,2000)
        self.assertEqual(room['phase'],'running')

    def test_wait_expiry_and_arrival_share_decision_batch(self):
        room,r,m,y=prepared()
        command(room,r,'wait')
        travel(room,m,'s059','s060',remaining=2)
        game.rail_step(room)
        game.rail_step(room)
        self.assertEqual(set(room['decision']['required']),{r['id'],m['id']})

    def test_change_lines_include_endpoints_and_valid_neighbors(self):
        room,r,m,y=prepared()
        for station_id in ADJACENCY:
            r['station']=station_id
            game.deal(room,r)
            for option in game.private_state(room,r,10000)['me']['availableLines']:
                line=next(line for line in LINES if line['id']==option['id'])
                self.assertEqual((option['start'],option['end']),(line['nodes'][0],line['nodes'][-1]))
                self.assertTrue(option['neighbors'])
                for neighbor in option['neighbors']:
                    self.assertEqual(abs(line['nodes'].index(station_id)-line['nodes'].index(neighbor)),1)

    def test_technical_recovery_countdown_and_preserved_private_state(self):
        room,r,m,y=prepared()
        m.update(corrupted=True,holdUntil=9)
        game.advance(room,15000)
        for now in (15100,16100,17100,18100):
            room['hostSeen']=now
            for p in room['players']:p['seen']=now
            game.advance(room,now)
        self.assertIsNone(room['technical'])
        self.assertTrue(m['corrupted'])
        self.assertEqual(m['holdUntil'],9)
        self.assertEqual(room['railAccum'],0)

    def test_public_projection_never_contains_private_state(self):
        room,r,m,y=prepared()
        m.update(corrupted=True,protectionUntil=56,bonusChanges=1)
        game.note(room,m,'SECRET-MESSAGE')
        public=game.public_state(room,10000)
        self.assertEqual([p['role'] for p in public['players']],['renko',None,None])
        text=json.dumps(public)
        for forbidden in ('hostToken','seed','hand','corrupted','holdUntil','protectionUntil','SECRET-MESSAGE','trip','bonusChanges','attempts'):
            self.assertNotIn(forbidden,text)
        personal=game.private_state(room,r,10000)
        self.assertNotIn('SECRET-MESSAGE',json.dumps(personal))
        self.assertNotIn(m['token'],json.dumps(personal))

    def test_full_match_simulations_keep_valid_state(self):
        for seed in range(20):
            room,r,m,y=prepared();room['seed']=str(seed)
            now=10000
            for i in range(500):
                if room['result']:break
                for p in room['players']:
                    if game.can_act(room,p):
                        cards=p['hand']
                        card=cards[(seed+i+p['id'])%len(cards)]
                        command(room,p,'travel',card=card['id'])
                now+=1000;room['hostSeen']=now
                for p in room['players']:p['seen']=now
                game.advance(room,now)
                json.dumps(room)
            if room['result']:
                self.assertIn(room['result']['outcome'],('hifuu','yukari','draw'))
            else:
                self.assertIn(room['phase'],('running','decision'))


if __name__=='__main__':
    unittest.main()
