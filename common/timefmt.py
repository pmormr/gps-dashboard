"""Canonical timestamp formatting — the single source of "now" across tiers.

Every timestamp column in the DB stores fixed-width millisecond UTC strings
(``2026-06-09T14:55:55.200Z``). Keeping the formatter here (rather than behind
``api.db``) lets the logger, the sensor readers, the processor, and the tools
share it without importing the database module — the timestamp shape is a
project-wide convention, not a database concern.

``api.db`` re-exports :func:`canonical_timestamp` and :func:`now_canonical` for
the many call sites that already import them from there.

The inverse also lives here, so one module owns how a stored timestamp becomes a
``datetime`` / number: :func:`parse_iso`, :func:`epoch_seconds`, and
:func:`age_seconds`.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Whole-second strftime template; canonical timestamps append a 3-digit
# millisecond fraction and a ``Z`` suffix to this (see ``format_canonical``).
_SECONDS_FORMAT = '%Y-%m-%dT%H:%M:%S'


def format_canonical(dt: datetime) -> str:
    """Format a datetime as fixed-width millisecond UTC text.

    Produces ``2026-06-09T14:55:55.200Z`` — whole seconds plus a zero-padded
    3-digit millisecond fraction and a ``Z`` suffix. The width is fixed so
    lexical ordering matches chronological ordering across every timestamp
    column; mixing widths breaks it, since ``'.'`` sorts before ``'Z'`` (a
    whole-second ``...55Z`` would sort *after* a millisecond ``...55.200Z``).

    Args:
        dt: A datetime; naive values are treated as UTC.

    Returns:
        The timestamp as fixed-width millisecond UTC text.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return f'{dt.strftime(_SECONDS_FORMAT)}.{dt.microsecond // 1000:03d}Z'


def canonical_timestamp(value: str) -> str:
    """Normalize an ISO-8601 timestamp to the canonical storage format.

    Every timestamp column stores fixed-width millisecond UTC strings
    (``2026-06-09T14:55:55.200Z``). The logger, marks, and sensor receipts write
    this form via ``now_canonical``; annotation bounds arrive from the browser as
    ``...000Z``. Collapsing every source to one width-aligned UTC format keeps the
    lexical range comparisons in the points, annotations, and sensor queries
    correct — and at 5 Hz the millisecond fraction makes raw fixes
    sub-second-distinct (a usable dedup key), which whole-second strings were not.

    Args:
        value: An ISO-8601 timestamp, with or without a ``Z`` suffix, an explicit
            offset, or fractional seconds.

    Returns:
        The timestamp as a fixed-width millisecond UTC string.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    return format_canonical(datetime.fromisoformat(value.replace('Z', '+00:00')))


def now_canonical() -> str:
    """Return the current time as a fixed-width millisecond canonical string.

    The single source for "now" timestamps written across tiers (the logger's
    raw inserts, the marks upsert, the sensor readers' receipts). Wall-clock
    ``now()`` is PPS-disciplined stratum-1 GPS time on the Pi, so it is
    GPS-quality without the backward jitter of the TPV ``time`` field.

    Returns:
        The current UTC time as fixed-width millisecond canonical text.
    """
    return format_canonical(datetime.now(UTC))


def parse_iso(value: str) -> datetime:
    """Parse a canonical/ISO-8601 timestamp to an aware UTC ``datetime``.

    The inverse of :func:`canonical_timestamp`: accepts the canonical ``...Z``
    storage form (and any ISO-8601 offset, or a naive value treated as UTC) and
    returns a timezone-aware datetime normalized to UTC.

    Args:
        value: An ISO-8601 timestamp, with or without a ``Z`` suffix, an explicit
            offset, or fractional seconds.

    Returns:
        The parsed instant as a timezone-aware UTC datetime.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def epoch_seconds(value: str) -> float:
    """Parse a canonical/ISO-8601 timestamp to Unix epoch seconds.

    Args:
        value: An ISO-8601 timestamp (see :func:`parse_iso`).

    Returns:
        Seconds since the Unix epoch as a float.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    return parse_iso(value).timestamp()


def age_seconds(value: str, now: datetime | None = None) -> float:
    """Return the age in seconds of a canonical/ISO-8601 timestamp.

    Args:
        value: An ISO-8601 timestamp (see :func:`parse_iso`).
        now: The reference instant; defaults to the current UTC time. Must be
            timezone-aware.

    Returns:
        ``now - value`` in seconds — positive when ``value`` is in the past.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    reference = now if now is not None else datetime.now(UTC)
    return (reference - parse_iso(value)).total_seconds()
