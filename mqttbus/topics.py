"""MQTT topic taxonomy helpers.

The sensor platform routes everything over a small, fixed topic tree on the local
mosquitto broker. This module is the single source of truth for building and
parsing those topic strings, so publishers, the ingest/alarm subscribers, and the
browser client all agree on the shape without re-implementing string splitting.

Taxonomy::

    sensors/<node>/<type>           # readings (JSON payload)
    sensors/<node>/<type>/status    # LWT, retained: "online" / "offline"
    alarms/<rule_id>                # alarm state, retained: "active" / "cleared"

``<node>`` is a short unique name (usually the physical location: ``cabin``,
``exterior``); ``<type>`` is the sensor type (``bme680``). The ``(node, type)``
pair is the stable stream key the ``sensors`` registry is keyed on.

This module intentionally has **no** dependency on a broker library, so it can be
imported anywhere (REST routes, tests) without pulling in ``paho-mqtt``.
"""

from dataclasses import dataclass
from typing import Literal

SENSORS_PREFIX = 'sensors'
ALARMS_PREFIX = 'alarms'
STATUS_SUFFIX = 'status'

#: Wildcard the ingest and alarm subscribers use to catch every sensor stream,
#: so a brand-new node is picked up with no Pi-side config change.
SENSORS_WILDCARD = f'{SENSORS_PREFIX}/#'

#: Wildcard the browser uses to catch every alarm state topic.
ALARMS_WILDCARD = f'{ALARMS_PREFIX}/#'

TopicKind = Literal['reading', 'status']


class TopicError(ValueError):
    """Raised when a node, type, or rule id cannot form a valid topic segment."""


@dataclass(frozen=True)
class SensorTopic:
    """A parsed ``sensors/...`` topic.

    Attributes:
        node: The ``<node>`` segment (stream location key).
        type: The ``<type>`` segment (sensor type).
        kind: ``'reading'`` for ``sensors/<node>/<type>`` or ``'status'`` for the
            retained LWT topic ``sensors/<node>/<type>/status``.
    """

    node: str
    type: str
    kind: TopicKind


def _validate_segment(segment: str, *, name: str) -> str:
    """Reject segments that would corrupt the topic tree.

    Args:
        segment: The candidate topic segment.
        name: Human-readable field name, used in the error message.

    Returns:
        The validated segment, unchanged.

    Raises:
        TopicError: If the segment is empty or contains ``/``, ``+``, or ``#``.
    """
    if not segment:
        raise TopicError(f'{name} must not be empty')
    bad = set(segment) & {'/', '+', '#'}
    if bad:
        raise TopicError(f"{name} {segment!r} must not contain {sorted(bad)}")
    return segment


def reading_topic(node: str, type: str) -> str:
    """Build the readings topic for a stream.

    Args:
        node: The node (location) name.
        type: The sensor type.

    Returns:
        ``sensors/<node>/<type>``.

    Raises:
        TopicError: If ``node`` or ``type`` is not a valid segment.
    """
    _validate_segment(node, name='node')
    _validate_segment(type, name='type')
    return f'{SENSORS_PREFIX}/{node}/{type}'


def status_topic(node: str, type: str) -> str:
    """Build the retained LWT status topic for a stream.

    Args:
        node: The node (location) name.
        type: The sensor type.

    Returns:
        ``sensors/<node>/<type>/status``.

    Raises:
        TopicError: If ``node`` or ``type`` is not a valid segment.
    """
    return f'{reading_topic(node, type)}/{STATUS_SUFFIX}'


def alarm_topic(rule_id: int) -> str:
    """Build the retained alarm-state topic for a rule.

    Args:
        rule_id: The ``alarm_rules`` row id.

    Returns:
        ``alarms/<rule_id>``.
    """
    return f'{ALARMS_PREFIX}/{rule_id}'


def parse_sensor_topic(topic: str) -> SensorTopic | None:
    """Parse a ``sensors/...`` topic into its parts.

    Args:
        topic: A concrete topic string received on the bus.

    Returns:
        A :class:`SensorTopic` if ``topic`` matches a readings or status topic, or
        ``None`` if it is not a recognized ``sensors/...`` topic (so callers can
        ignore unrelated traffic without raising).
    """
    parts = topic.split('/')
    if len(parts) == 3 and parts[0] == SENSORS_PREFIX:
        return SensorTopic(node=parts[1], type=parts[2], kind='reading')
    if (
        len(parts) == 4
        and parts[0] == SENSORS_PREFIX
        and parts[3] == STATUS_SUFFIX
    ):
        return SensorTopic(node=parts[1], type=parts[2], kind='status')
    return None
