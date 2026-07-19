"""Shared read helper for the sensor-readings tiers.

Both the sensors registry route and the fridge control plane need "the most
recent reading row for a sensor", picked from the per-type table named in
``READING_TABLES``. Keeping it here lets both import one implementation instead
of hand-rolling the same ``ORDER BY timestamp DESC LIMIT 1`` query.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from api.sensor_schema import READING_TABLES


def latest_reading(
    conn: sqlite3.Connection, sensor_id: int, sensor_type: str
) -> dict[str, Any] | None:
    """Return the most recent reading row for a sensor, or None.

    Args:
        conn: Open SQLite connection.
        sensor_id: ``sensors.id`` to read.
        sensor_type: Sensor type, selecting the readings table via
            ``READING_TABLES``.

    Returns:
        The latest reading as a dict (``timestamp`` + the type's metric columns),
        or None when the type is unknown or the sensor has no readings yet.
    """
    spec = READING_TABLES.get(sensor_type)
    if spec is None:
        return None
    cols = ', '.join(['timestamp', *spec['metrics']])
    row = conn.execute(
        f'SELECT {cols} FROM {spec["table"]} WHERE sensor_id = ? ORDER BY timestamp DESC LIMIT 1',
        (sensor_id,),
    ).fetchone()
    return dict(row) if row else None
