"""Fetch Wikipedia summaries + thumbnails for wiki-tagged places (laptop only — never the Pi).

Phase 7 of the POI expansion (``plans/attractions-poi-plan.md``): build the
``place_wiki`` cache — an offline blurb (+ photo) for every place whose OSM
tags name a Wikipedia article. ~166k NA rows carry a ``wikipedia`` title or a
``wikidata`` QID; this tool runs where the WAN lives, writes a standalone
*transfer DB*, and ``tools/import_places.py --wiki-db`` swaps it into the
sidecar on the Pi (full-replace, same ops shape as the OSM merge).

Pipeline::

    places.db (wiki-tagged rows) ──resolve QIDs──▶ REST summaries (+thumbs) ──▶ transfer .db

* Keys are ``api.db.place_wiki_key`` output (QID, else ``lang:Title``) — the
  same function the detail read resolves with, so cache and read can't drift.
* Rows tagged only with a QID resolve to their **English** article title via
  the Wikidata API (50 ids/request); entities without an enwiki sitelink are
  recorded as misses. Rows with an explicit ``wikipedia`` tag keep their tag's
  language.
* Summaries come from the Wikipedia REST ``page/summary`` endpoint (lead
  extract + thumbnail URL + canonical page URL). Extracts are CC BY-SA 4.0 —
  the UI attributes them in the detail sheet.
* The run is **resumable**: fetched keys and misses persist in the output DB
  and are skipped on re-run, so an interrupted 166k-page fetch loses nothing.
  Delete the output file to force a full refetch.

Examples::

    # Small smoke run (laptop dev DBs)
    uv run tools/fetch_wikipedia.py --db ~/osm-lab/dev-main.db \\
        --places-db ~/osm-lab/places.db -o ~/osm-lab/wiki-cache.db --limit 25

    # The real build (hours; resumable — re-run after any interruption)
    uv run tools/fetch_wikipedia.py --db ~/osm-lab/dev-main.db \\
        --places-db ~/osm-lab/places.db -o ~/osm-lab/wiki-cache.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from api.db import get_connection, init_db, now_canonical, place_wiki_key
from common.cli import run_cli

USER_AGENT = 'gps-dashboard/1.0 (https://github.com/pmormr/gps-dashboard)'
WIKIDATA_API = 'https://www.wikidata.org/w/api.php'
#: Article-language preference for QID sitelink resolution — English first,
#: then the other two NA languages (Mexico/Québec places often have no enwiki).
SITELINK_LANGS = (('enwiki', 'en'), ('eswiki', 'es'), ('frwiki', 'fr'))
QID_BATCH = 50
RETRIES = 5
RETRY_SLEEP = 5.0
#: Skip absurdly large "thumbnails" (some SVG renders); the standard summary
#: thumb is ~10–50 KB.
THUMB_MAX_BYTES = 1_000_000
BATCH_ROWS = 200
PROGRESS_EVERY = 1000

TRANSFER_SCHEMA = """
    CREATE TABLE IF NOT EXISTS place_wiki (
        wiki_key   TEXT PRIMARY KEY,
        title      TEXT NOT NULL,
        lang       TEXT NOT NULL,
        extract    TEXT NOT NULL,
        page_url   TEXT,
        thumb      BLOB,
        thumb_mime TEXT,
        fetched_at TEXT NOT NULL
    );
    -- Keys that resolved to nothing (deleted article, no enwiki sitelink, no
    -- extract): persisted so a resumed run skips them instead of re-asking.
    CREATE TABLE IF NOT EXISTS misses (
        wiki_key   TEXT PRIMARY KEY,
        reason     TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    );
"""


@dataclass(frozen=True)
class Target:
    """One article to fetch.

    Attributes:
        key: The ``place_wiki_key`` join key.
        lang: Article language (``'en'``…), or None until QID resolution.
        title: Article title, or None until QID resolution.
    """

    key: str
    lang: str | None
    title: str | None


@dataclass(frozen=True)
class WikiRow:
    """One fetched summary destined for the transfer DB.

    Attributes:
        key: The join key.
        title: Canonical article title (post-redirect).
        lang: Article language.
        extract: The lead extract (plain text).
        page_url: Canonical desktop page URL.
        thumb: Thumbnail bytes, or None.
        thumb_mime: Thumbnail content type, or None.
    """

    key: str
    title: str
    lang: str
    extract: str
    page_url: str | None
    thumb: bytes | None
    thumb_mime: str | None


def _tag_lang_title(details: dict) -> tuple[str, str] | None:
    """Extract ``(lang, title)`` from a ``wikipedia`` tag, if one parses.

    Mirrors :func:`api.db.place_wiki_key`'s normalisation of the tag so a
    QID-keyed row with an explicit article tag skips QID resolution.
    """
    tag = details.get('wikipedia')
    if not isinstance(tag, str) or not tag.strip():
        return None
    text = tag.strip().split(';', 1)[0].strip()
    if text.lower().startswith(('http://', 'https://')):
        return None
    lang, sep, title = text.partition(':')
    if not sep:
        lang, title = 'en', text
    lang, title = lang.strip().lower(), title.strip().replace('_', ' ')
    if not lang or not title:
        return None
    return lang, title


def collect_targets(conn: sqlite3.Connection) -> dict[str, Target]:
    """Scan the places tier for wiki-tagged rows and key them.

    One LIKE scan over the details column (~a minute at 10.7M rows); different
    places sharing an article collapse onto one key.

    Args:
        conn: An open connection with the places sidecar attached.

    Returns:
        ``key → Target`` (title present when any row's tag named it).
    """
    targets: dict[str, Target] = {}
    rows = conn.execute(
        'SELECT details FROM places '
        'WHERE details LIKE \'%"wikipedia"%\' OR details LIKE \'%"wikidata"%\''
    )
    for (details_json,) in rows:
        details = json.loads(details_json)
        key = place_wiki_key(details)
        if key is None:
            continue
        known = targets.get(key)
        if known is not None and known.title is not None:
            continue
        lang_title = _tag_lang_title(details)
        if lang_title is not None:
            targets[key] = Target(key, lang_title[0], lang_title[1])
        elif known is None:
            targets[key] = Target(key, None, None)
    return targets


def resolve_qids(session: requests.Session, qids: list[str]) -> dict[str, tuple[str, str]]:
    """Resolve QIDs to an article via the Wikidata API (en, then es/fr).

    Args:
        session: The shared HTTP session.
        qids: Bare QIDs (``'Q42'``) needing a title.

    Returns:
        ``qid → (lang, title)`` for entities with any preferred-language
        sitelink; ~most wikidata-only OSM tags reference items with *no*
        article in any language (measured 2026-07-10) — those stay absent.
    """
    resolved: dict[str, tuple[str, str]] = {}
    sitefilter = '|'.join(site for site, _ in SITELINK_LANGS)
    for start in range(0, len(qids), QID_BATCH):
        batch = qids[start : start + QID_BATCH]
        body = _get_with_retries(
            session,
            WIKIDATA_API,
            params={
                'action': 'wbgetentities',
                'ids': '|'.join(batch),
                'props': 'sitelinks',
                'sitefilter': sitefilter,
                'format': 'json',
            },
        )
        if body is None:
            continue
        for qid, entity in (body.json().get('entities') or {}).items():
            sitelinks = entity.get('sitelinks') or {}
            for site, lang in SITELINK_LANGS:
                title = (sitelinks.get(site) or {}).get('title')
                if title:
                    resolved[qid] = (lang, title)
                    break
        done = min(start + QID_BATCH, len(qids))
        if done % 5000 < QID_BATCH or done == len(qids):
            print(f'  … resolved {done:,}/{len(qids):,} QIDs', file=sys.stderr)
    return resolved


# Global politeness throttle: a shared minimum interval between request starts
# across all workers. Wikimedia rate-limits sustained parallel fetches (observed
# as 429 storms from upload.wikimedia.org on the first full run); concurrency
# then only hides latency, it never multiplies the request rate.
_throttle_lock = threading.Lock()
_next_slot = 0.0
_min_interval = 0.1


def _throttle() -> None:
    """Block until this worker's turn under the global rate limit."""
    global _next_slot
    with _throttle_lock:
        now = time.monotonic()
        wait = _next_slot - now
        _next_slot = max(now, _next_slot) + _min_interval
    if wait > 0:
        time.sleep(wait)


def _get_with_retries(
    session: requests.Session, url: str, params: dict | None = None
) -> requests.Response | None:
    """Throttled GET with backoff on transport errors and 429/5xx; None on a 404.

    A 429's ``Retry-After`` is honored when present (Wikimedia sends it);
    otherwise backoff grows linearly per attempt.

    Args:
        session: The shared HTTP session.
        url: Target URL.
        params: Optional query params.

    Returns:
        The successful response, or None when the resource does not exist.

    Raises:
        requests.RequestException: When all attempts fail.
    """
    last_exc: requests.RequestException | None = None
    for attempt in range(RETRIES):
        try:
            _throttle()
            response = session.get(url, params=params, timeout=30)
            if response.status_code == 404:
                return None
            if response.status_code in (429, 500, 502, 503):
                retry_after = response.headers.get('Retry-After', '')
                delay = float(retry_after) if retry_after.isdigit() else RETRY_SLEEP * (attempt + 1)
                time.sleep(max(delay, RETRY_SLEEP))
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(RETRY_SLEEP * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise requests.RequestException(f'retries exhausted for {url}')


def fetch_summary(
    session: requests.Session, target: Target, want_thumb: bool
) -> WikiRow | tuple[str, str]:
    """Fetch one article's summary (and thumbnail).

    Args:
        session: The shared HTTP session.
        target: The resolved target (lang + title set).
        want_thumb: Whether to download the thumbnail bytes.

    Returns:
        The :class:`WikiRow`, or ``(key, miss_reason)``.
    """
    assert target.lang is not None and target.title is not None
    encoded = quote(target.title.replace(' ', '_'), safe='')
    response = _get_with_retries(
        session, f'https://{target.lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}'
    )
    if response is None:
        return (target.key, 'not_found')
    body = response.json()
    extract = (body.get('extract') or '').strip()
    if not extract:
        return (target.key, 'no_extract')
    thumb = thumb_mime = None
    thumb_url = (body.get('thumbnail') or {}).get('source')
    if want_thumb and thumb_url:
        # A failed thumbnail must never lose the article — degrade to text-only
        # (the first full run dropped whole rows on Commons 429s).
        try:
            thumb_response = _get_with_retries(session, thumb_url)
        except requests.RequestException:
            thumb_response = None
        if thumb_response is not None and len(thumb_response.content) <= THUMB_MAX_BYTES:
            thumb = thumb_response.content
            thumb_mime = thumb_response.headers.get('Content-Type')
    return WikiRow(
        key=target.key,
        title=body.get('title') or target.title,
        lang=target.lang,
        extract=extract,
        page_url=((body.get('content_urls') or {}).get('desktop') or {}).get('page'),
        thumb=thumb,
        thumb_mime=thumb_mime,
    )


def _flush(out: sqlite3.Connection, rows: list[WikiRow], misses: list[tuple[str, str]]) -> None:
    """Write one batch of results (and misses) and commit."""
    now = now_canonical()
    out.executemany(
        'INSERT OR REPLACE INTO place_wiki '
        '(wiki_key, title, lang, extract, page_url, thumb, thumb_mime, fetched_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [(r.key, r.title, r.lang, r.extract, r.page_url, r.thumb, r.thumb_mime, now) for r in rows],
    )
    out.executemany(
        'INSERT OR REPLACE INTO misses (wiki_key, reason, fetched_at) VALUES (?, ?, ?)',
        [(key, reason, now) for key, reason in misses],
    )
    out.commit()
    rows.clear()
    misses.clear()


def run(args: argparse.Namespace) -> int:
    """Drive scan → QID resolution → concurrent summary fetch → transfer DB."""
    global _min_interval
    _min_interval = 1.0 / args.rps
    conn = get_connection()
    init_db(conn)
    print('Scanning places for wiki tags ...', file=sys.stderr)
    targets = collect_targets(conn)
    print(f'  {len(targets):,} distinct articles referenced', file=sys.stderr)

    out = sqlite3.connect(args.out)
    out.executescript(TRANSFER_SCHEMA)
    done = {row[0] for row in out.execute('SELECT wiki_key FROM place_wiki')}
    done |= {row[0] for row in out.execute('SELECT wiki_key FROM misses')}
    pending = [t for key, t in sorted(targets.items()) if key not in done]
    print(f'  {len(done):,} already fetched, {len(pending):,} to go', file=sys.stderr)
    if args.limit is not None:
        pending = pending[: args.limit]

    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT

    unresolved = [t.key for t in pending if t.title is None]
    if unresolved:
        print(f'Resolving {len(unresolved):,} QIDs via Wikidata ...', file=sys.stderr)
        titles = resolve_qids(session, unresolved)
        resolved: list[Target] = []
        no_sitelink: list[tuple[str, str]] = []
        for t in pending:
            if t.title is not None:
                resolved.append(t)
            elif t.key in titles:
                resolved.append(Target(t.key, *titles[t.key]))
            else:
                no_sitelink.append((t.key, 'no_sitelink'))
        print(f'  {len(no_sitelink):,} without an en/es/fr article (recorded)', file=sys.stderr)
        _flush(out, [], no_sitelink)
        pending = resolved

    stats: Counter[str] = Counter()
    rows: list[WikiRow] = []
    misses: list[tuple[str, str]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(fetch_summary, session, t, not args.no_thumbs) for t in pending]
        try:
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    result = future.result()
                except requests.RequestException as exc:
                    stats['error'] += 1
                    print(f'  ! {exc}', file=sys.stderr)
                    continue
                if isinstance(result, WikiRow):
                    stats['fetched'] += 1
                    stats['thumbs'] += result.thumb is not None
                    rows.append(result)
                else:
                    stats['missed'] += 1
                    misses.append(result)
                if len(rows) + len(misses) >= BATCH_ROWS:
                    _flush(out, rows, misses)
                if i % PROGRESS_EVERY == 0:
                    rate = i / max(time.monotonic() - started, 1e-9)
                    eta_min = (len(pending) - i) / max(rate, 1e-9) / 60
                    print(
                        f'  … {i:,}/{len(pending):,} ({rate:,.1f}/s, ~{eta_min:,.0f} min left)',
                        file=sys.stderr,
                    )
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            _flush(out, rows, misses)
            print(
                f'\nInterrupted. {stats["fetched"]:,} fetched ({stats["thumbs"]:,} thumbs), '
                f'{stats["missed"]:,} misses — saved; re-run to resume.',
                file=sys.stderr,
            )
            sys.exit(130)
    _flush(out, rows, misses)

    total = out.execute('SELECT COUNT(*) FROM place_wiki').fetchone()[0]
    size_mb = Path(args.out).stat().st_size / 1e6
    print(
        f'\n{stats["fetched"]:,} fetched ({stats["thumbs"]:,} thumbs), '
        f'{stats["missed"]:,} misses, {stats["error"]:,} errors '
        f'→ {total:,} rows in {args.out} ({size_mb:,.0f} MB)',
        file=sys.stderr,
    )
    return 0


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--db', help='Main SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)'
    )
    parser.add_argument(
        '--places-db',
        help='Places sidecar path (default: GPS_PLACES_DB_PATH, else places.db beside the main DB)',
    )
    parser.add_argument(
        '-o', '--out', default='wiki-cache.db', help='transfer DB output path (resumable)'
    )
    parser.add_argument('--limit', type=int, help='fetch at most N articles (smoke runs)')
    parser.add_argument('--no-thumbs', action='store_true', help='skip thumbnail downloads')
    parser.add_argument('--concurrency', type=int, default=8, help='fetch worker threads')
    parser.add_argument(
        '--rps', type=float, default=10.0, help='global request-rate cap across workers'
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: parse args, set the DB paths, run the fetcher."""
    args = parse_args()
    import api.db

    if args.db:
        api.db.DB_PATH = Path(args.db)
    if args.places_db:
        api.db.PLACES_DB_PATH = Path(args.places_db)
    sys.exit(run(args))


if __name__ == '__main__':
    run_cli(main)
