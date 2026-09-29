"""Server-only canon excerpts. Station bindings are this game's adaptations."""

from .settings import load_dialogues
from .data import STATION_BY_ID, EDGE_BY_ID

QUOTES = load_dialogues(STATION_BY_ID, EDGE_BY_ID)


def public_map_locations():
    """All possible locations, without roles, excerpts, or per-player progress."""
    stations = {}
    for quote in QUOTES.values():
        for station in quote['stations']:
            stations[station] = stations.get(station, True) and bool(quote.get('edge'))
    return [
        dict(station=station, expressOnly=express_only)
        for station, express_only in sorted(stations.items())
    ]


def candidates(player, entry):
    matches = [
        (key, quote)
        for key, quote in QUOTES.items()
        if quote['role'] == player.get('role')
        and entry['station'] in quote['stations']
        and (not quote.get('edge') or quote['edge'] == entry.get('edge'))
    ]
    # A completed express journey takes priority over the general Kyoto memory.
    return sorted(matches, key=lambda pair: (not bool(pair[1].get('edge')), pair[0]))


def public_dialogues(room, done=False):
    result = []
    for event in room.get('dialogues', []):
        quote = QUOTES[event['quote']]
        item = {key: event[key] for key in ('id', 'tick', 'player')}
        item['text'] = quote['text']
        if done:
            item.update(source=quote['source'], sourceUrl=quote['url'], mapping=quote['mapping'])
        result.append(item)
    return result
