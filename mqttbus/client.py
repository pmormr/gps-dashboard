"""Shared MQTT client helpers for the sensor platform.

A thin wrapper over ``paho-mqtt`` that centralizes broker connection settings
(host/port from the environment), automatic reconnect with backoff, and
Last-Will-and-Testament (LWT) registration, so the Pi reader and the ingest/alarm
subscribers don't each re-implement the boilerplate.

Broker location comes from the environment so dev and the Pi differ by config, not
code:

- ``GPS_MQTT_HOST`` (default ``localhost``)
- ``GPS_MQTT_PORT`` (default ``1883``)
"""

import os

import paho.mqtt.client as mqtt

DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 1883
KEEPALIVE_SECONDS = 60
RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 30


def broker_host() -> str:
    """Return the MQTT broker host from ``GPS_MQTT_HOST`` (default localhost)."""
    return os.environ.get('GPS_MQTT_HOST', DEFAULT_HOST)


def broker_port() -> int:
    """Return the MQTT broker port from ``GPS_MQTT_PORT`` (default 1883)."""
    return int(os.environ.get('GPS_MQTT_PORT', str(DEFAULT_PORT)))


def make_client(
    client_id: str,
    *,
    clean_session: bool = True,
    lwt_topic: str | None = None,
    lwt_payload: str | None = None,
) -> mqtt.Client:
    """Build a configured paho MQTT client.

    Sets a bounded reconnect backoff (so a transient broker outage self-heals like
    the GPS logger's reconnect loop) and, optionally, a retained LWT message the
    broker publishes if this client drops without a clean disconnect.

    Args:
        client_id: Stable client identifier. Required non-empty when
            ``clean_session`` is False, so the broker can resume the session.
        clean_session: False for subscribers that must not miss messages across a
            reconnect (the broker queues QoS-1 messages for a persistent session);
            True for publishers, which keep no server-side state.
        lwt_topic: Topic for the Last-Will-and-Testament, or None for no LWT.
        lwt_payload: Retained payload published to ``lwt_topic`` on ungraceful
            disconnect (e.g. ``"offline"``).

    Returns:
        An unconnected :class:`paho.mqtt.client.Client`. The caller wires callbacks,
        then calls ``connect`` + ``loop_start``/``loop_forever``.
    """
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=clean_session,
    )
    client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY, max_delay=RECONNECT_MAX_DELAY)
    if lwt_topic is not None:
        client.will_set(lwt_topic, lwt_payload, qos=1, retain=True)
    return client
