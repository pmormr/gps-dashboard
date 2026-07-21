"""Tests for the rigctld TCP-protocol client (``api/rigctld.py``).

The reply strings here are the real extended-mode wire format captured against a
Hamlib dummy backend (``rigctld -m 1``), so the parser is tested against bytes the
daemon actually emits. The socket I/O is faked, and the fake hands bytes back a few
at a time to exercise the partial-read reassembly in ``_read_reply``.
"""

from __future__ import annotations

import pytest

from api.rigctld import Reply, Rigctld, RigctldError, parse_reply

# --- parse_reply: pure framing logic --------------------------------------------------


def test_parse_labelled_field():
    r = parse_reply('get_freq:\nFrequency: 145000000\nRPRT 0\n')
    assert r == Reply(0, {'Frequency': '145000000'}, [])


def test_parse_multiple_fields():
    r = parse_reply('get_mode:\nMode: FM\nPassband: 15000\nRPRT 0\n')
    assert r.rprt == 0
    assert r.fields == {'Mode': 'FM', 'Passband': '15000'}


def test_parse_bare_value_drops_echo():
    # Single-value gets echo "get_level: RAWSTR" then a bare value line.
    r = parse_reply('get_level: RAWSTR\n-37\nRPRT 0\n')
    assert r.fields == {}
    assert r.values == ['-37']


def test_parse_error_rprt():
    r = parse_reply('set_freq: notanumber\nRPRT -1\n')
    assert r.rprt == -1
    assert r.fields == {}


def test_parse_missing_terminator_raises():
    with pytest.raises(RigctldError):
        parse_reply('get_freq:\nFrequency: 145000000\n')


# --- fake transport -------------------------------------------------------------------


class FakeSocket:
    """Canned-reply stand-in for a rigctld socket.

    Maps each sent command (the text after the ``+\\`` extended prefix, newline
    stripped) to a reply, and serves it back ``chunk`` bytes at a time so the
    client's read loop must reassemble it.
    """

    def __init__(self, table: dict[str, str], chunk: int = 3) -> None:
        self._table = table
        self._chunk = chunk
        self._buf = b''
        self.sent: list[str] = []

    def sendall(self, data: bytes) -> None:
        cmd = data.decode()
        self.sent.append(cmd)
        # Strip the extended "+\\" or non-extended "\\" prefix, and the newline.
        key = cmd.rstrip('\n')
        key = key[2:] if key.startswith('+\\') else key.removeprefix('\\')
        self._buf += self._table.get(key, 'RPRT -1\n').encode()

    def recv(self, _n: int) -> bytes:
        chunk, self._buf = self._buf[: self._chunk], self._buf[self._chunk :]
        return chunk

    def settimeout(self, _t: float) -> None: ...

    def close(self) -> None: ...


def make_rig(table: dict[str, str]) -> Rigctld:
    """A Rigctld wired to a FakeSocket, bypassing the real connect."""
    rig = Rigctld()
    rig._sock = FakeSocket(table)  # type: ignore[assignment]
    return rig


# --- getters: parsing through the client ----------------------------------------------


def test_get_freq():
    rig = make_rig({'get_freq': 'get_freq:\nFrequency: 146520000\nRPRT 0\n'})
    assert rig.get_freq() == 146520000


def test_get_mode():
    rig = make_rig({'get_mode': 'get_mode:\nMode: FMN\nPassband: 8000\nRPRT 0\n'})
    assert rig.get_mode() == ('FMN', 8000)


def test_get_level_bare_value():
    rig = make_rig({'get_level RAWSTR': 'get_level: RAWSTR\n142\nRPRT 0\n'})
    assert rig.get_level('RAWSTR') == 142.0


def test_get_level_unsupported_returns_none():
    # The real ID-5100 backend has no STRENGTH level; an RPRT error → None.
    rig = make_rig({'get_level STRENGTH': 'get_level: STRENGTH\nRPRT -11\n'})
    assert rig.get_level('STRENGTH') is None


def test_get_func_bare_value():
    rig = make_rig({'get_func TONE': 'get_func: TONE\n1\nRPRT 0\n'})
    assert rig.get_func('TONE') is True


def test_get_ctcss_tone_tenths():
    rig = make_rig({'get_ctcss_tone': 'get_ctcss_tone:\nCTCSS Tone: 1000\nRPRT 0\n'})
    assert rig.get_ctcss_tone() == 1000


def test_get_rptr_shift_and_offs():
    rig = make_rig(
        {
            'get_rptr_shift': 'get_rptr_shift:\nRptr Shift: +\nRPRT 0\n',
            'get_rptr_offs': 'get_rptr_offs:\nRptr Offset: 600000\nRPRT 0\n',
        }
    )
    assert rig.get_rptr_shift() == '+'
    assert rig.get_rptr_offs() == 600000


def test_get_dcd():
    rig = make_rig({'get_dcd': 'get_dcd:\nDCD: 0\nRPRT 0\n'})
    assert rig.get_dcd() is False


# --- raw CI-V reads (non-extended send_cmd_rx) ----------------------------------------

# The live wire exchange captured against the real rig (2026-07-18). The reply line is
# the single-wire bus echo of our frame + the rig's reply frame, hex-escaped, then a
# byte count — no RPRT. (The extended "+\\" form returns no reply bytes at all.)
DUALWATCH_READ = 'send_cmd_rx \\0xfe\\0xfe\\0x8c\\0xe0\\0x16\\0x59\\0xfd 15'
DUALWATCH_REPLY = (
    '\\0xFE\\0xFE\\0x8C\\0xE0\\0x16\\0x59\\0xFD'
    '\\0xFE\\0xFE\\0xE0\\0x8C\\0x16\\0x59\\0x01\\0xFD 15\n'
)


def test_read_civ_parses_reply_data():
    rig = make_rig({DUALWATCH_READ: DUALWATCH_REPLY})
    assert rig.read_civ(b'\x16\x59') == b'\x01'


def test_read_civ_short_reply_is_none():
    # Rig silent: rigctld returns only the bus echo of our own frame.
    rig = make_rig({DUALWATCH_READ: '\\0xFE\\0xFE\\0x8C\\0xE0\\0x16\\0x59\\0xFD 7\n'})
    assert rig.read_civ(b'\x16\x59') is None


def test_get_dualwatch_on_off_and_unreadable():
    assert make_rig({DUALWATCH_READ: DUALWATCH_REPLY}).get_dualwatch() is True
    off = DUALWATCH_REPLY.replace('\\0x01', '\\0x00')
    assert make_rig({DUALWATCH_READ: off}).get_dualwatch() is False
    assert make_rig({}).get_dualwatch() is None  # RPRT fallback line has no hex bytes


# --- setters: wire format + error mapping ---------------------------------------------


def test_set_freq_wire_format():
    sock = FakeSocket({'set_freq 146520000': 'set_freq: 146520000\nRPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.set_freq(146520000)
    assert sock.sent == ['+\\set_freq 146520000\n']


def test_set_func_encodes_bool():
    sock = FakeSocket({'set_func TONE 1': 'set_func: TONE 1\nRPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.set_func('TONE', True)
    assert sock.sent == ['+\\set_func TONE 1\n']


def test_set_ptt_encodes_bool():
    for on, code in ((True, 1), (False, 0)):
        sock = FakeSocket({f'set_ptt {code}': f'set_ptt: {code}\nRPRT 0\n'})
        rig = Rigctld()
        rig._sock = sock  # type: ignore[assignment]
        rig.set_ptt(on)
        assert sock.sent == [f'+\\set_ptt {code}\n']


def test_set_level_wire_format():
    sock = FakeSocket({'set_level AF 0.35': 'set_level: AF 0.35\nRPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.set_level('AF', 0.35)
    assert sock.sent == ['+\\set_level AF 0.35\n']


def test_send_civ_wire_format():
    # Reply format captured live against the ID-5100 (2026-07-02): the RPRT
    # terminator rides on the "Reply:" line, unlike every other command.
    cmd = 'send_cmd \\0xfe\\0xfe\\0x8c\\0xe0\\0x07\\0xd0\\0xfd'
    sock = FakeSocket({cmd: f'{cmd.split(" ", 1)[0]}: {cmd.split(" ", 1)[1]}\nReply: RPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.send_civ(b'\x07\xd0')
    assert sock.sent == [f'+\\{cmd}\n']


def test_set_powerstat_off_is_bare_18_00():
    # Power-off: no preamble, bare FE FE 8C E0 18 00 FD.
    cmd = 'send_cmd \\0xfe\\0xfe\\0x8c\\0xe0\\0x18\\0x00\\0xfd'
    sock = FakeSocket({cmd: 'send_cmd: ...\nReply: RPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.set_powerstat(False)
    assert sock.sent == [f'+\\{cmd}\n']


def test_set_powerstat_on_prepends_25_fe_preamble():
    # Power-on: 25x 0xFE wakeup preamble, then FE FE 8C E0 18 01 FD.
    preamble = '\\0xfe' * 25
    cmd = f'send_cmd {preamble}\\0xfe\\0xfe\\0x8c\\0xe0\\0x18\\0x01\\0xfd'
    sock = FakeSocket({cmd: 'send_cmd: ...\nReply: RPRT 0\n'})
    rig = Rigctld()
    rig._sock = sock  # type: ignore[assignment]
    rig.set_powerstat(True)
    assert sock.sent == [f'+\\{cmd}\n']


def test_send_civ_raises_on_rprt_error():
    cmd = 'send_cmd \\0xfe\\0xfe\\0x8c\\0xe0\\0x07\\0xd0\\0xfd'
    rig = make_rig({cmd: 'send_cmd: ...\nReply: RPRT -9\n'})
    with pytest.raises(RigctldError) as exc:
        rig.send_civ(b'\x07\xd0')
    assert exc.value.rprt == -9


def test_setter_raises_on_rprt_error():
    rig = make_rig({'set_freq 99': 'set_freq: 99\nRPRT -1\n'})
    with pytest.raises(RigctldError) as exc:
        rig.set_freq(99)
    assert exc.value.rprt == -1


def test_command_outside_context_raises():
    with pytest.raises(RigctldError):
        Rigctld().command('get_freq')
