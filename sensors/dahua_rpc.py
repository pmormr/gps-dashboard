"""Minimal Dahua RPC2 client (the JSON API behind the WebUI).

The legacy CGI on this fleet 501s host metrics and SMART, but every device
serves them over RPC2 — challenge-response login (two ``global.login`` POSTs,
MD5 per Dahua's scheme — the device's protocol, not a choice), then JSON method
calls carrying the *login response's* session id (it rotates from the
challenge's). Some services are object-style: ``<service>.factory.instance``
returns a handle passed back as a top-level ``object`` field.

Sessions are cheap, so the reader logs in per poll cycle rather than managing
keepalive/expiry. The NVR only speaks RPC2 over HTTPS (its HTTP→HTTPS redirect
drops POST bodies) with a self-signed cert; ``verify=False`` throughout — LAN
devices.
"""

from __future__ import annotations

import hashlib
from types import TracebackType
from typing import Any

import requests


class Rpc2Error(RuntimeError):
    """An RPC2 login failure or an error reply to a call."""


def password_digest(user: str, realm: str, random: str, password: str) -> str:
    """Compute the Dahua challenge-response password hash.

    Args:
        user: Login user name.
        realm: The ``realm`` from the login challenge.
        random: The ``random`` nonce from the login challenge.
        password: The account password.

    Returns:
        The uppercase hex digest the second ``global.login`` expects.
    """
    h1 = hashlib.md5(f'{user}:{realm}:{password}'.encode()).hexdigest().upper()  # noqa: S324
    return hashlib.md5(f'{user}:{random}:{h1}'.encode()).hexdigest().upper()  # noqa: S324


class Rpc2Session:
    """One logged-in RPC2 session (context manager; logs out on exit)."""

    def __init__(
        self,
        base: str,
        user: str,
        password: str,
        *,
        timeout: float = 5.0,
        http: requests.Session | None = None,
    ) -> None:
        """Args:
        base: Device base URL (``http://host`` or ``https://host``).
        user: Login user name.
        password: Account password.
        timeout: Per-request timeout in seconds.
        http: Optional shared ``requests.Session`` (a private one otherwise).
        """
        self._base = base
        self._user = user
        self._password = password
        self._timeout = timeout
        self._http = http or requests.Session()
        self._id = 0
        self._session_id: Any = None

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post(
            f'{self._base}/{path}', json=payload, timeout=self._timeout, verify=False
        )
        return resp.json()  # type: ignore[no-any-return]

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        path: str = 'RPC2',
        obj: Any = None,
        check: bool = True,
    ) -> dict[str, Any]:
        """Invoke one RPC2 method and return the full reply.

        Args:
            method: Method name (``service.method``).
            params: Method params, or None.
            path: URL path (``RPC2_Login`` during the login handshake).
            obj: Object handle for object-style services, or None.
            check: Raise :class:`Rpc2Error` on a ``result: false`` reply.

        Returns:
            The decoded JSON reply.

        Raises:
            Rpc2Error: When ``check`` and the device reports failure.
            requests.RequestException: On connection-level failure.
        """
        self._id += 1
        payload: dict[str, Any] = {'method': method, 'params': params, 'id': self._id}
        if self._session_id is not None:
            payload['session'] = self._session_id
        if obj is not None:
            payload['object'] = obj
        reply = self._post(path, payload)
        if check and not reply.get('result'):
            raise Rpc2Error(f'{method}: {reply.get("error")}')
        return reply

    def __enter__(self) -> Rpc2Session:
        """Perform the two-step login handshake."""
        challenge = self.call(
            'global.login',
            {'userName': self._user, 'password': '', 'clientType': 'Web3.0'},
            path='RPC2_Login',
            check=False,
        )
        self._session_id = challenge.get('session')
        params = challenge.get('params') or {}
        if 'realm' not in params or 'random' not in params:
            raise Rpc2Error(f'login challenge malformed: {challenge.get("error")}')
        digest = password_digest(self._user, params['realm'], params['random'], self._password)
        reply = self.call(
            'global.login',
            {
                'userName': self._user,
                'password': digest,
                'clientType': 'Web3.0',
                'authorityType': 'Default',
                'passwordType': 'Default',
            },
            path='RPC2_Login',
            check=False,
        )
        if not reply.get('result'):
            raise Rpc2Error(f'login rejected: {reply.get("error")}')
        self._session_id = reply.get('session', self._session_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Best-effort logout (the session would expire server-side anyway)."""
        if self._session_id is not None:
            try:
                self.call('global.logout', check=False)
            except requests.RequestException:
                pass
            self._session_id = None
