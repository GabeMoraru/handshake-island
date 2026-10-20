"""The line format between the worker and the host: one JSON object per line each way, ASCII only.

The worker imports this module, so it uses only the standard library.
"""

import json
import math

MAX_LINE = 8 * 1024 * 1024  # bytes; a whole conversation is about 22k tokens


def encode(value):
    """value as one line of compact JSON, without its newline."""
    return json.dumps(value, separators=(',', ':'), allow_nan=False)


def strict_json_loads(text):
    """Decode one JSON object, rejecting duplicate keys at any depth, NaN, Infinity, a fraction or exponent too large
    for a float, and an integer past Python's 4,300-digit limit. Nothing is repaired."""

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('Non-JSON numeric constant: ' + value)

    def finite(number):  # json.loads turns a number too large for a float, such as 1e400, into inf
        value = float(number)
        if not math.isfinite(value):
            raise ValueError('Number out of range: ' + number)
        return value

    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant, parse_float=finite)
    except RecursionError:  # callers treat ValueError as bad input; a RuntimeError would escape them
        raise ValueError('JSON is nested too deeply') from None
    if not isinstance(value, dict):
        raise ValueError('Expected one JSON object')
    return value
