"""Tests for the shared request-parameter validators.

Each helper returns a ``(value, error)`` pair; the error branch builds a Flask
response with :func:`flask.jsonify`, so those assertions run under the
``app_context`` fixture. Success branches need no context.
"""

from api.params import parse_bbox, parse_limit, parse_time


class TestParseBbox:
    def test_valid(self):
        bbox, err = parse_bbox({'bbox': '-106,39,-104,41'})
        assert err is None
        assert bbox == (-106.0, 39.0, -104.0, 41.0)

    def test_missing_is_not_an_error(self):
        assert parse_bbox({}) == (None, None)

    def test_wrong_field_count(self, app_context):
        bbox, err = parse_bbox({'bbox': '1,2,3'})
        assert bbox is None
        assert err[1] == 400

    def test_non_float(self, app_context):
        bbox, err = parse_bbox({'bbox': 'a,b,c,d'})
        assert bbox is None
        assert err[1] == 400

    def test_west_east_out_of_order(self, app_context):
        bbox, err = parse_bbox({'bbox': '10,2,3,4'})
        assert bbox is None
        assert err[1] == 400

    def test_south_north_out_of_order(self, app_context):
        bbox, err = parse_bbox({'bbox': '1,10,3,4'})
        assert bbox is None
        assert err[1] == 400


class TestParseLimit:
    def test_default_when_absent(self):
        limit, err = parse_limit({}, default=5000, maximum=20000)
        assert err is None
        assert limit == 5000

    def test_clamped_to_maximum(self):
        limit, err = parse_limit({'limit': '99999'}, default=5000, maximum=20000)
        assert err is None
        assert limit == 20000

    def test_parsed_value_under_maximum(self):
        limit, err = parse_limit({'limit': '100'}, default=5000, maximum=20000)
        assert err is None
        assert limit == 100

    def test_non_integer(self, app_context):
        limit, err = parse_limit({'limit': 'abc'}, default=5000, maximum=20000)
        assert limit is None
        assert err[1] == 400

    def test_zero_rejected(self, app_context):
        limit, err = parse_limit({'limit': '0'}, default=5000, maximum=20000)
        assert limit is None
        assert err[1] == 400

    def test_negative_rejected(self, app_context):
        limit, err = parse_limit({'limit': '-5'}, default=5000, maximum=20000)
        assert limit is None
        assert err[1] == 400


class TestParseTime:
    def test_valid_returns_canonical(self):
        value, err = parse_time('2026-06-09T14:55:55Z', 'start')
        assert err is None
        assert value == '2026-06-09T14:55:55.000Z'

    def test_invalid(self, app_context):
        value, err = parse_time('not-a-time', 'start')
        assert value is None
        assert err[1] == 400
