"""Tests for the sensor presentation spec (``api/sensor_schema.py``).

The load-bearing contract is that every stored metric column has a presentation
entry, so a new stream can't ship unlabelled — the drift that left Victron rendering
through the frontend's generic fallback. Also guards the small closed sets the client
switches on (conversion ids) and that ``/api/sensors`` actually serves the spec.
"""

from __future__ import annotations

from api.sensor_schema import METRIC_META, READING_TABLES

#: Conversion ids the client's CONVERTERS registry implements (static/js/sensors.js).
KNOWN_CONVERTS = {'c_to_f', 'kph_to_mph', 's_to_h'}

#: Codec ids the client's decodeCoded implements (web/src/lib/sensors.ts).
KNOWN_CODECS = {'enum', 'bitmask'}


def _all_columns() -> set[str]:
    """Return every metric column across all reading tables."""
    return {col for spec in READING_TABLES.values() for col in spec['metrics']}


def test_every_column_has_meta() -> None:
    """No stored column is missing a presentation entry (the Victron-gap guard)."""
    missing = _all_columns() - set(METRIC_META)
    assert not missing, f'columns without METRIC_META: {sorted(missing)}'


def test_meta_has_no_orphans() -> None:
    """No METRIC_META entry points at a column no table stores."""
    orphans = set(METRIC_META) - _all_columns()
    assert not orphans, f'METRIC_META entries with no column: {sorted(orphans)}'


def test_convert_ids_known() -> None:
    """Every convert id maps to a converter the client implements."""
    used = {m['convert'] for m in METRIC_META.values() if m['convert'] is not None}
    assert used <= KNOWN_CONVERTS, f'unknown convert ids: {sorted(used - KNOWN_CONVERTS)}'


def test_y_range_well_formed() -> None:
    """Every y_range is a ``[min, max]`` pair with min < max."""
    for col, meta in METRIC_META.items():
        rng = meta['y_range']
        if rng is not None:
            assert len(rng) == 2 and rng[0] < rng[1], f'{col}: bad y_range {rng}'


def test_codec_ids_known() -> None:
    """Every codec id maps to a decoder the client implements."""
    used = {m['codec'] for m in METRIC_META.values() if m['codec'] is not None}
    assert used <= KNOWN_CODECS, f'unknown codec ids: {sorted(used - KNOWN_CODECS)}'


def test_codec_and_codes_paired() -> None:
    """codec and codes are set together; codes is an int→str table; 0 keys bitmasks."""
    for col, meta in METRIC_META.items():
        codec, codes = meta['codec'], meta['codes']
        assert (codec is None) == (codes is None), f'{col}: codec/codes must be paired'
        if codes is None:
            continue
        assert all(isinstance(k, int) for k in codes), f'{col}: codes keys must be int'
        assert all(isinstance(v, str) for v in codes.values()), f'{col}: codes values must be str'
        if codec == 'bitmask':
            assert 0 in codes, f'{col}: bitmask codes need a 0 (all-clear) label'


def test_throttled_is_bitmask() -> None:
    """The Pi throttle column decodes as a bitmask with the expected flag bits."""
    meta = METRIC_META['throttled']
    assert meta['codec'] == 'bitmask'
    assert meta['codes'] is not None
    assert meta['codes'][0x10000] and meta['codes'][0x40000]  # under-volt + throttled (sticky)


def test_api_sensors_serves_meta(client) -> None:
    """``/api/sensors`` carries the presentation spec for the viewer to render from."""
    body = client.get('/api/sensors').get_json()
    assert set(body['meta']) == _all_columns()
    assert body['meta']['rpm']['label'] == 'RPM'
