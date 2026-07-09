"""Places tier API — nearby-places and event-schedule reads.

The read surface over the tier synced by ``tools/import_places.py`` (see
``.claude/modules/places.md``). List reads return the queryable columns plus the
``summary`` teaser; the full ``details`` JSON (tour stops, operating hours,
amenities, fees) is fetched per-row by the detail endpoints, so browsing a
region never drags megabytes of display structure.

Every payload carries ``synced_at`` — schedule data is only as fresh as the last
sync, and the UI is expected to wear that age rather than present it as live.

* ``GET /api/places`` — bbox/kind/category/rank/park/search-filtered POI list.
  Rows without a coordinate never match a bbox (their lat/lon is NULL) but
  appear in unfiltered or park/search-filtered reads. ``q`` searches the FTS5
  index (name + summary + category/kind) with **token-prefix** semantics:
  ``creek`` matches "Clear Creek Trail", but mid-token substrings do not
  ("lear" no longer matches "Clear" — the LIKE-era contract changed with the
  broad-POI tier). ``max_rank`` is the pin-zoom gate (1 major … 5 micro) —
  at ~10M broad rows, "all pins in view" is only a valid query above the gate.
  Searches whose match set is too large to score (junk prefixes — see
  ``_FTS_UNBOUNDED_MAX``) are answered from a bounded bm25 candidate pool,
  trading recall for latency on exactly the queries where ranking millions
  of matches is meaningless anyway.
* ``GET /api/places/<id>`` — one POI with parsed ``details``.
* ``GET /api/places/events`` — occurrences (event × date × time window) in
  a calendar-date window, grouped per event. Dates are park-local
  ``YYYY-MM-DD`` strings as published — not the ms-UTC axis.
* ``GET /api/places/events/<id>`` — one event with parsed ``details`` and
  its full occurrence list.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, Response, jsonify, request

from api.db import get_connection
from api.params import parse_bbox, parse_limit

places_bp = Blueprint('places', __name__)

_PLACE_COLS = (
    'id',
    'source',
    'source_kind',
    'source_id',
    'park_code',
    'name',
    'lat',
    'lon',
    'summary',
    'synced_at',
    'category',
    'rank',
)
_PLACE_COLUMNS = ', '.join(_PLACE_COLS)
#: p.-qualified projection for queries that join places_fts (whose column names
#: shadow the content table's).
_PLACE_COLUMNS_Q = ', '.join(f'p.{c}' for c in _PLACE_COLS)
_EVENT_COLUMNS = (
    'id, source, source_id, park_code, name, lat, lon, location_text, '
    'is_free, needs_reservation, synced_at'
)
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

#: Above this many FTS matches, ``q`` switches to bounded-candidate mode:
#: bm25 scores every match before anything else applies, so a junk-prefix
#: query ('c', 'park' — millions of rows NA-wide) costs seconds. Below it the
#: unbounded join keeps full recall: bbox/category/rank filters see every
#: match. Counting matches first is cheap (doclist sizes, no scoring).
_FTS_UNBOUNDED_MAX = 60_000
#: Candidate pool in bounded mode — the top-N by bm25 *before* the other
#: filters apply, so recall degrades on those queries (a bbox'd search only
#: sees in-bbox rows that made the NA-wide top-N). Acceptable: only match
#: sets too unspecific to rank meaningfully ever hit this path.
_FTS_CANDIDATE_LIMIT = 10_000


def _parse_date(value: str | None, name: str) -> tuple[str | None, tuple[Response, int] | None]:
    """Validate an optional ``YYYY-MM-DD`` calendar-date param.

    Event dates are park-local calendar dates (not ms-UTC timestamps), so this
    is a distinct axis from :func:`api.params.parse_time`.

    Args:
        value: The raw param value, or None when absent.
        name: Field name for the error message.

    Returns:
        ``(value, None)`` on success (None passes through), or
        ``(None, error_response)``.
    """
    if value is None or _DATE_RE.match(value):
        return value, None
    return None, (jsonify({'error': f"Invalid date for '{name}': {value} (want YYYY-MM-DD)"}), 400)


def _fts_query(q: str) -> str | None:
    """Turn user search text into an FTS5 token-prefix query.

    Each whitespace-ish token becomes a quoted prefix term (``"clear"* "cr"*``,
    implicit AND) — quoting neutralises FTS5 operators (OR/NOT/NEAR/parens) in
    user text, and the regex strips the quote/star characters that could break
    out of it.

    Args:
        q: Raw ``q`` param text.

    Returns:
        The FTS5 MATCH expression, or None when no searchable token survives
        (the caller falls back to a LIKE scan — decision 8's internal fallback).
    """
    tokens = re.findall(r'[^\s"*,()]+', q)
    if not tokens:
        return None
    return ' '.join(f'"{tok}"*' for tok in tokens)


@places_bp.get('/api/places')
def list_places():
    """POI list: ``bbox``, ``kind``/``category`` (comma-separated), ``max_rank``,
    ``park``, ``q``, ``center`` — all optional.

    ``q`` searches the FTS5 index with token-prefix semantics (module
    docstring). Search results order by match quality → rank → distance to
    ``center`` (``lon,lat`` — pass the map center, or the current fix when
    there's no map context); non-search reads order by rank → name, so the
    most significant places survive ``limit`` truncation.
    """
    bbox, err = parse_bbox(request.args)
    if err:
        return err
    limit, err = parse_limit(request.args, default=2000, maximum=10000)
    if err:
        return err
    center: tuple[float, float] | None = None
    center_arg = request.args.get('center')
    if center_arg:
        try:
            lon_text, lat_text = center_arg.split(',')
            center = (float(lon_text), float(lat_text))
        except ValueError:
            return jsonify({'error': f'Invalid center: {center_arg} (want lon,lat)'}), 400
    max_rank_arg = request.args.get('max_rank')
    max_rank: int | None = None
    if max_rank_arg is not None:
        try:
            max_rank = int(max_rank_arg)
        except ValueError:
            return jsonify({'error': f'Invalid max_rank: {max_rank_arg}'}), 400

    where: list[str] = []
    params: list = []
    if bbox is not None:
        w, s, e, n = bbox
        where += ['p.lat BETWEEN ? AND ?', 'p.lon BETWEEN ? AND ?']
        params += [s, n, w, e]
    for column, arg in (('source_kind', 'kind'), ('category', 'category')):
        value = request.args.get(arg)
        if value:
            wanted = [v.strip() for v in value.split(',') if v.strip()]
            where.append(f'p.{column} IN ({",".join("?" * len(wanted))})')
            params += wanted
    if max_rank is not None:
        where.append('p.rank <= ?')
        params.append(max_rank)
    park = request.args.get('park')
    if park:
        where.append('p.park_code = ?')
        params.append(park)

    conn = get_connection()
    order: list[str] = []
    q = request.args.get('q')
    if q:
        match = _fts_query(q)
        if match is not None:
            n_matches = conn.execute(
                'SELECT count(*) FROM places_fts WHERE places_fts MATCH ?', (match,)
            ).fetchone()[0]
            if n_matches > _FTS_UNBOUNDED_MAX:
                sql = (
                    f'SELECT {_PLACE_COLUMNS_Q} FROM ('
                    'SELECT rowid, bm25(places_fts) AS fts_score FROM places_fts '
                    f'WHERE places_fts MATCH ? ORDER BY fts_score LIMIT {_FTS_CANDIDATE_LIMIT}'
                    ') fts JOIN places p ON p.id = fts.rowid'
                )
                order.append('fts.fts_score')
            else:
                sql = (
                    f'SELECT {_PLACE_COLUMNS_Q} FROM places_fts '
                    'JOIN places p ON p.id = places_fts.rowid'
                )
                where.insert(0, 'places_fts MATCH ?')
                order.append('bm25(places_fts)')
            params.insert(0, match)
        else:
            sql = f'SELECT {_PLACE_COLUMNS_Q} FROM places p'
            where.append('p.name LIKE ?')
            params.append(f'%{q}%')
    else:
        sql = f'SELECT {_PLACE_COLUMNS_Q} FROM places p'
    # A rank gate excludes NULL ranks, so plain `rank` is equivalent — and on
    # non-bbox reads it lets idx_places_rank_name serve the top-N without
    # sorting the whole gate tier (~45 s at 10.7M rows). Bbox'd reads keep
    # COALESCE on purpose: it stops the planner preferring the rank index
    # over the latlon partials (a sparse bbox would scan the entire tier).
    if bbox is None and max_rank is not None:
        order.append('p.rank')
    else:
        order.append('COALESCE(p.rank, 9)')
    if center is not None:
        # Squared-degree distance: a tiebreaker, not a measurement — the
        # ~cos(lat) lon compression doesn't change who's "nearby" enough
        # to matter behind match quality and rank. NULL-coord rows sort last,
        # not first (NULL is smaller than every number in ASC).
        order.append('COALESCE((p.lat - ?) * (p.lat - ?) + (p.lon - ?) * (p.lon - ?), 1e18)')
        params += [center[1], center[1], center[0], center[0]]
    order.append('p.name ASC')

    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += f' ORDER BY {", ".join(order)} LIMIT ?'
    rows = [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]
    return jsonify({'places': rows, 'count': len(rows), 'truncated': len(rows) == limit})


@places_bp.get('/api/places/<int:place_id>')
def get_place(place_id: int):
    """One POI with its full parsed ``details`` (tour stops, hours, amenities…)."""
    conn = get_connection()
    row = conn.execute(
        f'SELECT {_PLACE_COLUMNS}, details FROM places WHERE id = ?',
        (place_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'place not found'}), 404
    record = dict(row)
    record['details'] = json.loads(record['details'])
    return jsonify(record)


@places_bp.get('/api/places/events')
def list_events():
    """Occurrences in a calendar window, grouped per event.

    ``start``/``end`` bound the occurrence date (``YYYY-MM-DD``, both optional);
    ``bbox``/``park`` filter the event. ``limit`` caps *occurrences* (the join
    rows); an event's ``dates`` list holds only the occurrences that matched.
    Ordered by each event's first matching occurrence.
    """
    start, err = _parse_date(request.args.get('start'), 'start')
    if err:
        return err
    end, err = _parse_date(request.args.get('end'), 'end')
    if err:
        return err
    bbox, err = parse_bbox(request.args)
    if err:
        return err
    limit, err = parse_limit(request.args, default=500, maximum=5000)
    if err:
        return err

    where: list[str] = []
    params: list = []
    if start is not None:
        where.append('d.date >= ?')
        params.append(start)
    if end is not None:
        where.append('d.date <= ?')
        params.append(end)
    if bbox is not None:
        w, s, e, n = bbox
        where += ['e.lat BETWEEN ? AND ?', 'e.lon BETWEEN ? AND ?']
        params += [s, n, w, e]
    park = request.args.get('park')
    if park:
        where.append('e.park_code = ?')
        params.append(park)

    columns = ', '.join(f'e.{c.strip()}' for c in _EVENT_COLUMNS.split(','))
    sql = (
        f'SELECT {columns}, d.date, d.time_start, d.time_end '
        'FROM place_event_dates d JOIN place_events e ON e.id = d.event_id'
    )
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY d.date ASC, d.time_start ASC LIMIT ?'
    conn = get_connection()
    rows = conn.execute(sql, [*params, limit]).fetchall()

    events: dict[int, dict] = {}
    for row in rows:
        record = dict(row)
        occurrence = {
            'date': record.pop('date'),
            'time_start': record.pop('time_start'),
            'time_end': record.pop('time_end'),
        }
        events.setdefault(record['id'], {**record, 'dates': []})['dates'].append(occurrence)
    return jsonify(
        {
            'events': list(events.values()),
            'count': len(events),
            'truncated': len(rows) == limit,
        }
    )


@places_bp.get('/api/places/events/<int:event_id>')
def get_event(event_id: int):
    """One event with parsed ``details`` and its full occurrence list."""
    conn = get_connection()
    row = conn.execute(
        f'SELECT {_EVENT_COLUMNS}, details FROM place_events WHERE id = ?',
        (event_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'event not found'}), 404
    record = dict(row)
    record['details'] = json.loads(record['details'])
    record['dates'] = [
        dict(r)
        for r in conn.execute(
            'SELECT date, time_start, time_end FROM place_event_dates '
            'WHERE event_id = ? ORDER BY date ASC, time_start ASC',
            (event_id,),
        ).fetchall()
    ]
    return jsonify(record)
