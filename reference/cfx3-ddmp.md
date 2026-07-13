# Dometic CFX3 — DDMP protocol notes

Wire-format notes for the van fridge (CFX3 75DZ) app protocol, for off-grid
reference. Community reverse engineering (keshavdv/dometic-cfx3) supplied the
topic table and encodings; the history-bucket semantics, tail-byte behavior, and
session quirks below were pinned against the real unit with `tools/cfx3_probe.py`
(survey + `--watch` runs, 2026-07-13, fridge at `192.168.42.185`). Production
implementation: `common/ddmp.py` (protocol core), `sensors/fridge_reader.py`
(poll policy), `api/routes/fridge.py` (control plane).

## Transport & framing

- TCP server on the fridge's WiFi module, port **13142** (`CFX_HOST`/`CFX_PORT`).
- Frames are CR-delimited JSON: `{"ddmp": [action, p0, p1, p2, p3, *value_bytes]}\r`.
  All array elements are byte-valued ints; multi-byte values are little-endian.
- Actions (byte 0): `0 PUBLISH · 1 SUBSCRIBE · 2 PING · 3 HELLO · 4 ACK · 5 NAK
  · 6 NOP`.
- Flow: client SUBSCRIBEs a 4-byte topic param → fridge ACKs the subscribe and
  PUBLISHes the value (before or after the ACK — attribute by param, not order);
  every incoming PING and PUBLISH **must be ACKed back** — the fridge retries
  unACKed publishes and then drops the client. NOP frames arrive interleaved;
  ignore them.
- Writes are `[PUBLISH, *param, *encoded_value]` from the client; the fridge
  answers ACK (applied) or NAK (refused).
- **One app client at a time.** A held connection locks out the phone app (and
  vice versa). Every consumer here uses short connect→work→disconnect sessions;
  concurrent connects surface as transport errors (retry once, briefly).
- Unauthenticated on the trusted van LAN. The `STATION_*`/`CFX_DIRECT_*`
  password topics exist in the protocol; nothing here touches them.

## Topic table (params this project touches)

Stored snapshot topics (`common.ddmp.TOPICS`, → `fridge_readings` columns):

| column | param | type |
|---|---|---|
| comp0_temp_c / comp1_temp_c | `[0\|16, 1, 1, 1]` | ddegC |
| comp0_set_c / comp1_set_c (**writable**) | `[0\|16, 2, 1, 1]` | ddegC |
| comp0_door_open / comp1_door_open | `[0\|16, 8, 1, 1]` | bool |
| comp0_power / comp1_power (**writable**) | `[0\|16, 0, 1, 1]` | bool |
| cooler_power | `[0, 0, 3, 1]` | bool |
| power_source (0 AC · 1 DC · 2 Solar) | `[0, 5, 3, 1]` | u8 |
| input_voltage_v | `[0, 1, 3, 1]` | dV |
| battery_protection (0 Low · 1 Med · 2 High) | `[0, 2, 3, 1]` | u8 |
| temp_alert_cc / temp_alert_dcm | `[0, 0\|3, 5, 1]` | bool |
| door_alert / voltage_alert | `[0, 1\|2, 5, 1]` | bool |

Control-plane reads (`api/routes/fridge.py`, fetched once per process):

| topic | param | decode |
|---|---|---|
| allowed setpoint range | `[0\|16, 128, 1, 1]` | 2 × int16-LE ÷10 → **−22.0 .. 10.0 °C** (both zones) |
| recommended range | `[0\|16, 129, 1, 1]` | same → **−15.0 .. 4.0 °C** (both zones) |
| presented temp unit | `[0, 0, 2, 1]` | u8: 0 = °C, 1 = °F (this unit: 1) |

History topics (`common.ddmp.HISTORY_PARAMS`, → `fridge_history` rows):

| topic | param |
|---|---|
| DC current history hour / day / week | `[0, 64\|65\|66, 3, 1]` |
| compartment temp history (unused — temps already sampled 60 s) | `[0\|16, 64\|65\|66, 1, 1]` |

Deliberately not subscribed: the NTC/compressor/fan error topics (`p2=4`) — the
reference repo's params for them are self-described mock-broker values.

## Value encodings

- `ddegC`: int16-LE, signed, decidegrees C (`-150` = −15.0 °C). Write encoding
  uses `ceil(temp_c * 10)` (vendor-app rounding).
- `dV`: uint16-LE decivolts. `bool`: one 0/1 byte. `u8`: one byte.
- `INT16_ARRAY` (ranges): consecutive int16-LE pairs ÷10, `[min, max]`.

## Probed history semantics (`HISTORY_DATA_ARRAY`)

One 15-byte publish per subscribe (no chunking observed): **7 × int16-LE signed
deci-amps** + one trailing tick byte.

- **Bucket order: newest first.** Index 0 is the *in-progress* bucket, updating
  live as its window accumulates; on a roll the array shifts right (index 0's
  final value lands at index 1, the oldest falls off index 6).
- **Buckets are sliding, fridge-internal windows — NOT wall-aligned.** Each
  bucket is **256 ticks** of a span-specific tick; the tail byte (`data[14]`)
  is the tick counter (0..255, span-wide — identical across a span's DC and
  temp topics) and the bucket rolls when it wraps. An early "rolls on the wall
  hour" reading was a coincidence of one roll landing near 17:00 — an
  eliminated hypothesis; don't re-derive it from a single observation.
- **Measured widths** (watch runs, 2026-07-13): hour-span rolls 607–608 s apart
  (tick ≈ 2.35 s) → modeled **600 s**; day-span tick ≈ 1/min → modeled
  **4 h (14 400 s)**; week-span tick ≈ 1/6 min → modeled **24 h (86 400 s)**
  (`common.ddmp.HISTORY_BUCKET_S`). So the spans cover roughly the trailing
  70 min / 28 h / 7 d in 7 buckets each. Day/week widths carry more
  measurement error than hour (fewer observed rolls).
- Values are the bucket's average DC draw in deci-amps: this unit idles
  ~0.3–0.6 A at ~26.6 V input, with ~1.5–1.8 A compressor-heavy buckets.
- **Production mapping** (`sensors/fridge_reader.flatten_history`): the
  in-progress bucket's absolute start = poll time − tail × (width/256), snapped
  to a quarter-width grid so re-polls UPSERT the same `fridge_history` rows;
  earlier buckets step back one width. The snap absorbs tick jitter and small
  fridge-clock drift (worst-case mislabeling ≤ width/8).
- The fridge occasionally re-publishes a topic within one session (ACK race);
  take the last frame.

## Session timing (probe measurements)

Connect ~80–1100 ms on the van WiFi; each subscribe answers within tens of ms.
A full 14-topic survey session runs ~2 s of real work (the probe's quiet-wait
padding dominates its ~29 s wall time). The production reader's whole-session
deadline is 15 s.
