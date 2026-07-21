"""The van's Dahua recording fleet — device identity, no I/O.

Split out of ``dahua_reader.py`` so consumers can reuse the fleet table without
pulling in the MQTT publisher stack: the health reader (``dahua_reader``), the
probe (``tools/dahua_probe.py``), and the web app's camera routes
(``api/routes/cameras.py``) all import from here. Kept dependency-free (a
dataclass + a tuple) — importing it must not drag in ``requests``/``paho``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    """One fleet member."""

    node: str
    host: str
    is_nvr: bool

    @property
    def rpc_base(self) -> str:
        """The device's RPC2 base URL — the NVR only speaks RPC2 over HTTPS."""
        return f'{"https" if self.is_nvr else "http"}://{self.host}'


#: The active Dahua fleet (vault hostnames). Hikvision cams (.55/.56) are out of
#: scope — different API (ISAPI), not recording.
FLEET: tuple[Device, ...] = (
    Device('van-nvr', '192.168.42.50', is_nvr=True),
    Device('van-cam-front', '192.168.42.51', is_nvr=False),
    Device('van-cam-blind-left', '192.168.42.52', is_nvr=False),
    Device('van-cam-blind-right', '192.168.42.53', is_nvr=False),
    Device('van-cam-rear', '192.168.42.54', is_nvr=False),
)

#: Node → sensor type, the ``run_fleet_publisher`` stream map.
FLEET_STREAMS: dict[str, str] = {d.node: ('nvr' if d.is_nvr else 'camera') for d in FLEET}
