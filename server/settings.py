"""Load authored configuration once at startup, with errors tied to file/field names.

These files are server-only. The HTTP layer explicitly selects public fields;
never expose the whole configuration directory through a static route.
"""

import json
import math
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_DIR = Path(__file__).resolve().parent.parent / 'config'


def load_json(name):
    path = CONFIG_DIR / name
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        raise ValueError(f'config/{name}: {error}') from error
    if not isinstance(value, dict):
        raise ValueError(f'config/{name}: expected a JSON object')
    return value


def require(condition, field, description):
    if not condition:
        raise ValueError(f'{field}: {description}')


def fields(value, expected, field):
    require(isinstance(value, dict), field, 'expected an object')
    require(set(value) == set(expected), field, f'expected fields: {", ".join(expected)}')


def integer(value, field, minimum=0):
    require(type(value) is int and value >= minimum, field, f'expected integer >= {minimum}')


def probability(value, field):
    require(
        type(value) in (int, float) and 0 <= value <= 1, field, 'expected probability in [0, 1]'
    )


def validate_rules(rules):
    field = 'config/rules.json'
    fields(rules, ('tick_ms', 'dialogue', 'events'), field)
    integer(rules['tick_ms'], f'{field}.tick_ms', 1)
    dialogue = rules['dialogue']
    fields(
        dialogue,
        (
            'start_tick',
            'cooldown_ticks',
            'max_per_player',
            'first_probabilities',
            'repeat_probability',
        ),
        f'{field}.dialogue',
    )
    for key in ('start_tick', 'cooldown_ticks', 'max_per_player'):
        integer(dialogue[key], f'{field}.dialogue.{key}', 1 if key == 'max_per_player' else 0)
    probabilities = dialogue['first_probabilities']
    require(
        isinstance(probabilities, list) and bool(probabilities),
        f'{field}.dialogue.first_probabilities',
        'expected a nonempty array',
    )
    for index, value in enumerate(probabilities):
        probability(value, f'{field}.dialogue.first_probabilities[{index}]')
    probability(dialogue['repeat_probability'], f'{field}.dialogue.repeat_probability')
    events = rules['events']
    fields(
        events,
        ('kyoto_protection_probability', 'kyoto_dialogue_probability', 'nagano_probability'),
        f'{field}.events',
    )
    for key, value in events.items():
        probability(value, f'{field}.events.{key}')
    return rules


def validate_starts(starts):
    field = 'config/starts.json'
    fields(
        starts, ('min_distance_ticks', 'max_distance_ticks', 'max_distance_ratio', 'groups'), field
    )
    integer(starts['min_distance_ticks'], f'{field}.min_distance_ticks', 1)
    integer(
        starts['max_distance_ticks'], f'{field}.max_distance_ticks', starts['min_distance_ticks']
    )
    ratio = starts['max_distance_ratio']
    require(
        type(ratio) in (int, float) and math.isfinite(ratio) and ratio >= 1,
        f'{field}.max_distance_ratio',
        'expected a finite number >= 1',
    )
    groups = starts['groups']
    require(
        isinstance(groups, list) and bool(groups), f'{field}.groups', 'expected a nonempty array'
    )
    for index, group in enumerate(groups):
        require(
            isinstance(group, list)
            and len(group) == 3
            and all(isinstance(station, str) for station in group)
            and len(set(group)) == 3,
            f'{field}.groups[{index}]',
            'expected three distinct station IDs',
        )
    return starts


def validate_messages(messages):
    field = 'config/messages.json'
    fields(
        messages,
        (
            'kyoto_protection',
            'maribel_corrupted',
            'yukari_corruption_notice',
            'kyoto_dialogue',
            'nagano_hold',
            'nara_bonus',
        ),
        field,
    )
    for key, value in messages.items():
        require(
            isinstance(value, str) and bool(value.strip()),
            f'{field}.{key}',
            'expected nonempty text',
        )
    return messages


def load_dialogues(stations, edges):
    """Validate cross-references after the network has loaded (avoids an import cycle)."""
    quotes = load_json('dialogues.json')
    required = {'role', 'group', 'stations', 'text', 'source', 'url', 'mapping'}
    for key, quote in quotes.items():
        field = f'config/dialogues.json.{key}'
        require(isinstance(quote, dict), field, 'expected an object')
        require(required <= quote.keys() <= required | {'edge'}, field, 'invalid quote fields')
        for name in ('group', 'text', 'source', 'url', 'mapping'):
            require(
                isinstance(quote[name], str) and bool(quote[name].strip()),
                f'{field}.{name}',
                'expected nonempty text',
            )
        require(
            quote['role'] in ('maribel', 'yukari'), f'{field}.role', 'expected maribel or yukari'
        )
        places = quote['stations']
        require(
            isinstance(places, list)
            and bool(places)
            and all(isinstance(station, str) and station in stations for station in places),
            f'{field}.stations',
            'expected known station IDs',
        )
        if 'edge' in quote:
            edge = quote['edge']
            require(isinstance(edge, str) and edge in edges, f'{field}.edge', 'unknown edge ID')
            require(
                set(places) <= {edges[edge]['source'], edges[edge]['target']},
                f'{field}.stations',
                'must be endpoints of the required edge',
            )
        url = urlsplit(quote['url'])
        require(
            url.scheme in ('http', 'https')
            and bool(url.hostname)
            and url.username is None
            and url.password is None,
            f'{field}.url',
            'expected an HTTP(S) source URL without credentials',
        )
    return quotes


RULES = validate_rules(load_json('rules.json'))
STARTS = validate_starts(load_json('starts.json'))
MESSAGES = validate_messages(load_json('messages.json'))
