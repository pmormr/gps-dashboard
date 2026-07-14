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
* ``GET /api/places/<id>`` — one POI with parsed ``details``, plus its cached
  Wikipedia summary (``wiki``) when the ``place_wiki`` cache has the row's
  wiki tag (resolved at read time — see ``api.db.place_wiki_key``).
* ``GET /api/places/<id>/photo`` — the cached Wikipedia thumbnail bytes.
* ``GET /api/places/events`` — occurrences (event × date × time window) in
  a calendar-date window, grouped per event. Dates are park-local
  ``YYYY-MM-DD`` strings as published — not the ms-UTC axis.
* ``GET /api/places/events/<id>`` — one event with parsed ``details`` and
  its full occurrence list.
"""

from __future__ import annotations

import json
import math
import re

from flask import Blueprint, Response, jsonify, request

from api.db import get_connection, place_wiki_key
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

#: Above this many FTS matches, a *bbox'd* ``q`` switches to bounded-candidate
#: mode: bm25 scores every match before anything else applies, so a
#: junk-prefix query ('c', 'park' — millions of rows NA-wide) costs seconds.
#: Below it the unbounded join keeps full recall: bbox/category/rank filters
#: see every match. Counting matches first is cheap (doclist sizes, no
#: scoring). Without a bbox the gate is _FTS_CANDIDATE_LIMIT instead — the
#: join itself (one places lookup per match) is what costs seconds at tens of
#: thousands of matches ('coffee', 55k ≈ 2.7 s), a bbox is what makes it
#: cheap, and an unscoped search is pure relevance-ranking anyway, so the
#: top-of-pool slice is the answer by definition.
_FTS_UNBOUNDED_MAX = 60_000
#: Candidate pool in bounded mode — the top-N by bm25 *before* the other
#: filters apply, so recall degrades on those queries (a bbox'd search only
#: sees in-bbox rows that made the NA-wide top-N). Acceptable: only match
#: sets too unspecific to rank meaningfully ever hit this path.
_FTS_CANDIDATE_LIMIT = 10_000
#: Facet counts run over at most this many in-scope rows. Exact facets over a
#: no-bbox FTS join cost seconds at 10M rows (55k-match GROUP BY ≈ 2 s local);
#: an unordered sample this size is ~15 ms with proportions that track the
#: exact counts. Counts are exact whenever the scope fits inside the sample —
#: ``facets_sampled`` tells the client which it got.
_FACET_SAMPLE = 10_000
#: Facet rows returned (distinct kinds, by descending count).
_FACET_LIMIT = 20
#: Metres per degree of latitude — the scale for the ``radius`` circle filter.
_M_PER_DEG_LAT = 111_320.0

#: Display-time twin unification (unification plan, decision 6): source
#: preference when one physical feature has rows in several sources — the
#: curated side wins ('Bear Lake' the NPS interpretive page over the bare OSM
#: water row). GNIS is deliberately absent: its true OSM twins are already
#: dropped at import, and what survives (towns, category-incompatible name
#: hits) is *not* the same feature — grouping it would collapse a town into a
#: shop that shares its name.
_TWIN_SOURCE_RANK = {'nps': 0, 'ridb': 1, 'osm': 2}
#: Same ~1 km proximity box as the GNIS import dedupe.
_TWIN_LAT_HALF = 0.01
_TWIN_LON_HALF = 0.015


def _twin_ref(row: dict) -> dict:
    """The compact cross-link a kept row carries for one grouped-out twin."""
    return {
        'id': row['id'],
        'source': row['source'],
        'source_id': row['source_id'],
        'source_kind': row['source_kind'],
    }


def group_twins(rows: list[dict]) -> list[dict]:
    """Collapse cross-source same-feature rows within one returned page.

    Exact name (casefolded) within the ~1 km box across *different* sources in
    :data:`_TWIN_SOURCE_RANK` = one feature: the preferred source's row keeps
    the page position and carries ``twins`` refs for the rows it absorbed
    (``source_id`` included so the client can suppress a dropped OSM twin's
    basemap mark too). Same-source rows never group — two nearby cafes of one
    chain are two places. Page-local by design: twins co-occur in bbox'd
    reads, and a search page holds every match for the name.

    Args:
        rows: The ordered page of place dicts (mutated: ``twins`` keys added).

    Returns:
        The page with absorbed twins removed.
    """
    kept: list[dict] = []
    heads: dict[str, list[int]] = {}
    for row in rows:
        rank = _TWIN_SOURCE_RANK.get(row['source'])
        name, lat, lon = row['name'], row['lat'], row['lon']
        if rank is None or not name or lat is None or lon is None:
            kept.append(row)
            continue
        key = name.casefold()
        merged = False
        for i in heads.get(key, []):
            head = kept[i]
            if (
                head['source'] != row['source']
                and abs(head['lat'] - lat) < _TWIN_LAT_HALF
                and abs(head['lon'] - lon) < _TWIN_LON_HALF
            ):
                if rank < _TWIN_SOURCE_RANK[head['source']]:
                    row['twins'] = [*head.pop('twins', []), _twin_ref(head)]
                    kept[i] = row
                else:
                    kept[i].setdefault('twins', []).append(_twin_ref(row))
                merged = True
                break
        if not merged:
            heads.setdefault(key, []).append(len(kept))
            kept.append(row)
    return kept


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
    ``park``, ``q``, ``center``, ``facets`` — all optional.

    ``q`` searches the FTS5 index with token-prefix semantics (module
    docstring). Search results order by exact-name match (case-insensitive)
    → match quality → rank → distance to ``center`` (``lon,lat`` — pass the
    map center, or the current fix when there's no map context); non-search
    reads order by rank → name, so the most significant places survive
    ``limit`` truncation.

    ``facets=1`` adds type-refinement counts: distinct ``source_kind`` values
    within the scope of every filter *except* ``kind`` (so a selected kind chip
    doesn't collapse the row to itself), counted over at most
    ``_FACET_SAMPLE`` in-scope rows — ``facets_sampled`` is true when the
    sample capped and counts are proportions rather than totals.

    ``radius`` (metres, requires ``center``, mutually exclusive with ``bbox``)
    scopes the read to a circle: internally it synthesizes the bounding bbox
    (so the latlon indexes, the FTS gate, and facets behave exactly like a
    bbox'd read) plus a corner-trimming circle clause, so ``limit``/
    ``truncated`` count the circle, not the square.
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
    radius_arg = request.args.get('radius')
    radius: float | None = None
    cos_lat = 1.0
    if radius_arg is not None:
        try:
            radius = float(radius_arg)
        except ValueError:
            return jsonify({'error': f'Invalid radius: {radius_arg}'}), 400
        if not math.isfinite(radius) or radius <= 0:
            return jsonify({'error': f'Invalid radius: {radius_arg} (want metres > 0)'}), 400
        if center is None:
            return jsonify({'error': 'radius requires center'}), 400
        if bbox is not None:
            return jsonify({'error': 'radius and bbox are mutually exclusive'}), 400
        lat_half = radius / _M_PER_DEG_LAT
        # The same ~cos(lat) lon compression as the ordering tiebreak; floored
        # like the frontend's box math so polar centers can't explode the box.
        cos_lat = max(0.2, math.cos(math.radians(center[1])))
        lon_half = lat_half / cos_lat
        bbox = (
            center[0] - lon_half,
            center[1] - lat_half,
            center[0] + lon_half,
            center[1] + lat_half,
        )

    # Filters shared by the list read and the facet counts — everything except
    # the kind filter, which stays in its own clause so facets can scope to
    # "all the other filters" (a selected kind must not collapse the facet row
    # to itself).
    where: list[str] = []
    params: list = []
    if bbox is not None:
        w, s, e, n = bbox
        where += ['p.lat BETWEEN ? AND ?', 'p.lon BETWEEN ? AND ?']
        params += [s, n, w, e]
    if radius is not None and center is not None:
        # Circle in the locally-scaled plane: trims the bbox corners so
        # `truncated` counts the circle, not the square. No trig in SQL —
        # cos²(lat) and (radius in degrees)² arrive precomputed.
        where.append('((p.lat - ?) * (p.lat - ?) + (p.lon - ?) * (p.lon - ?) * ?) <= ?')
        params += [
            center[1],
            center[1],
            center[0],
            center[0],
            cos_lat**2,
            (radius / _M_PER_DEG_LAT) ** 2,
        ]
    kind_where: list[str] = []
    kind_params: list = []
    for column, arg, clauses, values in (
        ('source_kind', 'kind', kind_where, kind_params),
        ('category', 'category', where, params),
    ):
        value = request.args.get(arg)
        if value:
            wanted = [v.strip() for v in value.split(',') if v.strip()]
            clauses.append(f'p.{column} IN ({",".join("?" * len(wanted))})')
            values += wanted
    if max_rank is not None:
        where.append('p.rank <= ?')
        params.append(max_rank)
    park = request.args.get('park')
    if park:
        where.append('p.park_code = ?')
        params.append(park)

    conn = get_connection()
    order: list[str] = []
    order_params: list = []
    base_from = 'FROM places p'
    base_params: list = []
    q = request.args.get('q')
    if q:
        # Exact-name matches outrank everything: bm25 normalizes by whole-
        # document length across all four indexed columns, so a row whose
        # summary is longer scores *worse* than a longer-named neighbour
        # ('Leadville' the town loses to 'Leadville Mountain'). Searching a
        # thing's exact name must find that thing first. In bounded-candidate
        # mode the boost only reorders the pool — an exact-named row bm25
        # ranked out of the pool stays out (junk-prefix queries only).
        order.append('p.name = ? COLLATE NOCASE DESC')
        order_params.append(q.strip())
        match = _fts_query(q)
        if match is not None:
            n_matches = conn.execute(
                'SELECT count(*) FROM places_fts WHERE places_fts MATCH ?', (match,)
            ).fetchone()[0]
            unbounded_max = _FTS_UNBOUNDED_MAX if bbox is not None else _FTS_CANDIDATE_LIMIT
            if n_matches > unbounded_max:
                base_from = (
                    'FROM (SELECT rowid, bm25(places_fts) AS fts_score FROM places_fts '
                    f'WHERE places_fts MATCH ? ORDER BY fts_score LIMIT {_FTS_CANDIDATE_LIMIT}'
                    ') fts JOIN places p ON p.id = fts.rowid'
                )
                base_params.append(match)
                order.append('fts.fts_score')
            else:
                base_from = 'FROM places_fts JOIN places p ON p.id = places_fts.rowid'
                where.insert(0, 'places_fts MATCH ?')
                params.insert(0, match)
                order.append('bm25(places_fts)')
        else:
            where.append('p.name LIKE ?')
            params.append(f'%{q}%')
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
        order_params += [center[1], center[1], center[0], center[0]]
    order.append('p.name ASC')

    sql = f'SELECT {_PLACE_COLUMNS_Q} {base_from}'
    if where or kind_where:
        sql += ' WHERE ' + ' AND '.join(where + kind_where)
    sql += f' ORDER BY {", ".join(order)} LIMIT ?'
    rows = [
        dict(r)
        for r in conn.execute(
            sql, [*base_params, *params, *kind_params, *order_params, limit]
        ).fetchall()
    ]
    truncated = len(rows) == limit
    rows = group_twins(rows)
    payload = {'places': rows, 'count': len(rows), 'truncated': truncated}

    if request.args.get('facets') == '1':
        inner = f'SELECT p.source_kind AS kind {base_from}'
        if where:
            inner += ' WHERE ' + ' AND '.join(where)
        inner += f' LIMIT {_FACET_SAMPLE}'
        groups = conn.execute(
            f'SELECT kind, COUNT(*) AS n FROM ({inner}) GROUP BY kind ORDER BY n DESC, kind',
            [*base_params, *params],
        ).fetchall()
        payload['facets'] = [{'kind': g['kind'], 'count': g['n']} for g in groups[:_FACET_LIMIT]]
        payload['facets_sampled'] = sum(g['n'] for g in groups) >= _FACET_SAMPLE
    return jsonify(payload)


@places_bp.get('/api/places/lookup')
def lookup_place():
    """Resolve a ``(source, source_id)`` natural key to its tier row id.

    The basemap tap-through bridge: a pois mark's planetiler feature id
    decodes client-side to an OSM ``source_id`` (``node/…``/``way/…``); this
    read maps it onto the tier's numeric id via the unique natural-key index.
    404 means the tier doesn't carry the feature (archive/tier vintage skew or
    an excluded kind) — the client falls back to the minimal tile-attrs sheet.
    """
    source = request.args.get('source')
    source_id = request.args.get('source_id')
    if not source or not source_id:
        return jsonify({'error': 'source and source_id are required'}), 400
    conn = get_connection()
    row = conn.execute(
        'SELECT id FROM places WHERE source = ? AND source_id = ?', (source, source_id)
    ).fetchone()
    if row is None:
        return jsonify({'error': 'place not found'}), 404
    return jsonify({'id': row['id']})


@places_bp.get('/api/places/<int:place_id>')
def get_place(place_id: int):
    """One POI with its full parsed ``details`` (tour stops, hours, amenities…).

    ``wiki`` carries the cached Wikipedia summary (title, extract, page_url,
    has_thumb — the blob itself is served by the photo endpoint) or null; the
    extract is CC BY-SA 4.0, which the UI must attribute.

    ``twins`` cross-links the same physical feature's rows in other sources
    (same casefolded name within ~1 km, :data:`_TWIN_SOURCE_RANK` sources
    only) — display-time unification, the data itself keeps every row.
    """
    conn = get_connection()
    row = conn.execute(
        f'SELECT {_PLACE_COLUMNS}, details FROM places WHERE id = ?',
        (place_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'place not found'}), 404
    record = dict(row)
    record['details'] = json.loads(record['details'])
    record['twins'] = []
    if record['name'] and record['lat'] is not None and record['source'] in _TWIN_SOURCE_RANK:
        # Box-first via the latlon index (a ±1 km box is a few hundred rows);
        # an FTS-first join on a generic name would score millions of matches.
        candidates = conn.execute(
            'SELECT id, source, source_id, source_kind, name FROM places '
            'WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? '
            'AND id != ? AND source != ? AND name = ? COLLATE NOCASE',
            (
                record['lat'] - _TWIN_LAT_HALF,
                record['lat'] + _TWIN_LAT_HALF,
                record['lon'] - _TWIN_LON_HALF,
                record['lon'] + _TWIN_LON_HALF,
                place_id,
                record['source'],
                record['name'],
            ),
        ).fetchall()
        record['twins'] = [
            _twin_ref(dict(c)) for c in candidates if c['source'] in _TWIN_SOURCE_RANK
        ]
    record['wiki'] = None
    wiki_key = place_wiki_key(record['details'])
    if wiki_key is not None:
        wiki = conn.execute(
            'SELECT title, lang, extract, page_url, thumb IS NOT NULL AS has_thumb, '
            'fetched_at FROM place_wiki WHERE wiki_key = ?',
            (wiki_key,),
        ).fetchone()
        if wiki is not None:
            record['wiki'] = {**dict(wiki), 'has_thumb': bool(wiki['has_thumb'])}
    return jsonify(record)


@places_bp.get('/api/places/<int:place_id>/photo')
def get_place_photo(place_id: int):
    """The cached Wikipedia thumbnail for one place (404 when none is cached)."""
    conn = get_connection()
    row = conn.execute('SELECT details FROM places WHERE id = ?', (place_id,)).fetchone()
    if row is None:
        return jsonify({'error': 'place not found'}), 404
    wiki_key = place_wiki_key(json.loads(row['details']))
    if wiki_key is not None:
        wiki = conn.execute(
            'SELECT thumb, thumb_mime FROM place_wiki WHERE wiki_key = ? AND thumb IS NOT NULL',
            (wiki_key,),
        ).fetchone()
        if wiki is not None:
            return Response(
                wiki['thumb'],
                mimetype=wiki['thumb_mime'] or 'image/jpeg',
                headers={'Cache-Control': 'public, max-age=86400'},
            )
    return jsonify({'error': 'no photo'}), 404


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
