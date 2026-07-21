<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { errMsg } from '../lib/errors'

  import {
    getRadioStatus,
    getRadioTransmissions,
    getSoundboard,
    postRadio,
    transmitRadio,
    type RadioStatus,
    type RadioTransmission,
    type SoundboardClip,
  } from '../lib/api'
  import { parseWaveform, RAWSTR_S9, sMeter } from '../lib/radio'
  import { startListen, type ListenSession } from '../lib/radioListen'
  import Toast from '../lib/Toast.svelte'
  import { useToast } from '../lib/useToast.svelte'
  import WaveformPlayer from '../lib/WaveformPlayer.svelte'
  import WaveformStrip from '../lib/WaveformStrip.svelte'

  // Standard CTCSS tones (Hz) the ID-5100 supports (from dump_caps).
  const CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2,
    110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2,
    165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5,
    206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
  ]
  const MODES = [
    { v: 'FM', l: 'FM' },
    { v: 'FMN', l: 'FM-N' },
    { v: 'AM', l: 'AM' },
    { v: 'D-STAR', l: 'D-STAR' },
  ]
  const TONE_MODES = [
    { v: 'off', l: 'Off' },
    { v: 'tone', l: 'Tone (TX)' },
    { v: 'tsql', l: 'TSQL' },
  ]
  const SHIFTS = [
    { v: 'simplex', l: 'Simplex' },
    { v: 'plus', l: '+' },
    { v: 'minus', l: '−' },
  ]
  // The rig's three TX power steps as normalized Hamlib RFPOWER values, captured
  // live 2026-07-02: sets snap to 42/128/213 (÷255). These are the *setting*
  // steps — the CI-V power-meter scale (26/77/255) is a different axis.
  const RF_POWERS = [
    { v: 42 / 255, l: 'Low' },
    { v: 128 / 255, l: 'Mid' },
    { v: 213 / 255, l: 'High' },
  ]

  // The race-net cross-band preset (the event setup): the 2m race net relayed to
  // a 70cm HT link. The 70cm side runs tone squelch (TSQL) so only the tone-tagged
  // HT gets retransmitted onto the net; 2m stays open. Tweak freqs/tone/power here.
  const RACE_PRESET = {
    a: { freq_hz: 147_555_000, mode: 'FM', tone: { mode: 'off' } }, // 2m race net (open)
    b: { freq_hz: 446_175_000, mode: 'FM', tone: { mode: 'tsql', hz: 203.5 } }, // 70cm HT link (TSQL)
    rfpower: 213 / 255, // High — the van repeater wants range
    main: 'a', // keep 2m as Main
  } as const

  // Transmission log. BLIP_S sits just above the recorder's minimum capture
  // length (~5.2 s: pre-roll + hang), so the hide-blips filter drops the
  // touchscreen-beep/kerchunk captures without touching real short traffic.
  const TX_PAGE = 25
  const BLIP_S = 6
  let txs = $state<RadioTransmission[]>([])
  let txTotal = $state(0)
  let txLoading = $state(false)
  let txError = $state('')
  let hideBlips = $state(false)
  let expandedId = $state<number | null>(null)

  // Transmit console (operator-clicked TX). Soundboard clips + TTS text; every
  // send keys the rig, so `sending` locks the whole console for the duration.
  let clips = $state<SoundboardClip[]>([])
  let engines = $state<string[]>(['espeak'])
  let ttsText = $state('')
  let engine = $state('espeak')
  let sending = $state(false)

  // Per-transmission tuning, persisted so a dialed-in rate/delay survives reloads.
  // rate = TTS speed multiplier (50% = half speed); settle = post-key delay so the
  // receiving rigs open squelch before the first word.
  const loadPref = (k: string, d: number): number => {
    const raw = localStorage.getItem(k)
    const v = raw == null ? NaN : Number(raw)
    return Number.isFinite(v) ? v : d
  }
  // Key is versioned (…V2) so the raised 75% default supersedes any value an
  // earlier build auto-persisted, rather than being masked by it.
  let ratePct = $state(loadPref('radioTtsRatePctV2', 75))
  let settleMs = $state(loadPref('radioSettleMs', 250))
  $effect(() => {
    localStorage.setItem('radioTtsRatePctV2', String(ratePct))
  })
  $effect(() => {
    localStorage.setItem('radioSettleMs', String(settleMs))
  })

  // Live listen (WHEP → MediaMTX hub). Independent of the CI-V control plane, so
  // it stays available even when the rig readout is offline.
  type ListenState = 'idle' | 'connecting' | 'live' | 'error'
  let listenState = $state<ListenState>('idle')
  let listenErr = $state('')
  let listenVol = $state(loadPref('radioListenVol', 80))
  let audioEl = $state<HTMLAudioElement>()
  let session: ListenSession | null = null

  function stopSession(): void {
    session?.close()
    session = null
    if (audioEl) audioEl.srcObject = null
  }
  function onListenClosed(): void {
    // Fired only when the peer drops on its own — a user Stop sets 'idle' first.
    if (listenState === 'live') {
      listenState = 'error'
      listenErr = 'Stream dropped'
    }
    stopSession()
  }
  async function toggleListen(): Promise<void> {
    if (listenState === 'connecting' || listenState === 'live') {
      listenState = 'idle'
      stopSession()
      return
    }
    listenState = 'connecting'
    listenErr = ''
    try {
      session = await startListen(undefined, onListenClosed)
      if (audioEl) {
        audioEl.srcObject = session.stream
        audioEl.volume = listenVol / 100
        audioEl.muted = sending
        await audioEl.play().catch(() => {}) // click gesture allows playback; ignore edge rejections
      }
      listenState = 'live'
    } catch (e) {
      stopSession()
      listenErr = errMsg(e)
      listenState = 'error'
    }
  }
  $effect(() => {
    if (audioEl) audioEl.volume = listenVol / 100
    localStorage.setItem('radioListenVol', String(listenVol))
  })
  // Monitor-feedback trap (R10): silence the monitor while keying so your own
  // TX audio doesn't blast back through the listener.
  $effect(() => {
    if (audioEl && listenState === 'live') audioEl.muted = sending
  })

  // Cross-band repeater staging (1.5e). Advanced/occasional — collapsed by
  // default. Persisted as one blob so a dialed-in pair survives reloads.
  interface XbForm {
    aFreq: number
    aMode: string
    aTone: number // CTCSS Hz; 0 = no tone
    bFreq: number
    bMode: string
    bTone: number
    power: number // normalized RFPOWER (matches RF_POWERS)
    main: 'a' | 'b'
  }
  const XB_DEFAULT: XbForm = {
    aFreq: 146.52,
    aMode: 'FM',
    aTone: 0,
    bFreq: 445.0,
    bMode: 'FM',
    bTone: 0,
    power: 42 / 255,
    main: 'a',
  }
  function loadXb(): XbForm {
    try {
      return { ...XB_DEFAULT, ...JSON.parse(localStorage.getItem('radioXbForm') ?? '{}') }
    } catch {
      return XB_DEFAULT
    }
  }
  let xb = $state<XbForm>(loadXb())
  let xbShow = $state(false)
  let xbStaging = $state(false)
  $effect(() => {
    localStorage.setItem('radioXbForm', JSON.stringify(xb))
  })

  // Rig power (1.5e). Fire-and-forget CI-V — the rig can't report power state, so
  // these are actions, not a toggle. Power-off gets a two-tap confirm.
  let powering = $state(false)
  let powerConfirm = $state(false)
  let powerConfirmTimer: number | undefined

  let s = $state<RadioStatus | null>(null)
  let freqInput = $state<number | null>(null)
  let ctcss = $state(100)
  let offsetKhz = $state(600)
  let afPct = $state(0)
  let sqlPct = $state(0)
  let editingTone = $state(false)
  let editingOffset = $state(false)
  let editingAf = $state(false)
  let editingSql = $state(false)

  let pollTimer: number | undefined

  const toaster = useToast(2200)

  async function poll(): Promise<void> {
    try {
      const next = await getRadioStatus()
      s = next
      // Re-sync controls to the rig, but never clobber a field being edited.
      if (next.online) {
        if (!editingTone && next.ctcss_tone_hz != null) ctcss = next.ctcss_tone_hz
        if (!editingOffset && next.rptr_offset_hz != null) offsetKhz = next.rptr_offset_hz / 1000
        if (!editingAf && next.levels?.af != null) afPct = Math.round(next.levels.af * 100)
        if (!editingSql && next.levels?.sql != null) sqlPct = Math.round(next.levels.sql * 100)
      }
    } catch {
      s = { online: false, error: 'Could not reach the server' }
    }
  }

  async function write(path: string, body: unknown, after?: () => void): Promise<void> {
    try {
      await postRadio(path, body)
      await poll()
      after?.()
    } catch (e) {
      toaster.toast(errMsg(e), true)
    }
  }

  async function submitFreq(): Promise<void> {
    if (!freqInput || freqInput <= 0) return
    await write('/api/radio/freq', { hz: Math.round(freqInput * 1e6) }, () => (freqInput = null))
  }
  const setMode = (m: string): Promise<void> => write('/api/radio/mode', { mode: m })
  const applyTone = (m: string): Promise<void> =>
    write('/api/radio/tone', m === 'off' ? { mode: m } : { mode: m, hz: ctcss })
  const applyShift = (sh: string): Promise<void> =>
    write(
      '/api/radio/repeater',
      sh === 'simplex' ? { shift: sh } : { shift: sh, offset_hz: Math.round(offsetKhz * 1000) }
    )

  const setBand = (b: string): Promise<void> => write('/api/radio/band', { band: b })
  const setDualwatch = (on: boolean): Promise<void> => write('/api/radio/dualwatch', { on })
  const setLevel = (level: string, value: number): Promise<void> =>
    write('/api/radio/level', { level, value })
  const rfActive = (v: number): boolean =>
    s?.online === true && s.levels?.rfpower != null && Math.abs(s.levels.rfpower - v) < 0.05

  async function loadTxs(reset: boolean): Promise<void> {
    txLoading = true
    txError = ''
    try {
      const page = await getRadioTransmissions({
        limit: TX_PAGE,
        minS: hideBlips ? BLIP_S : undefined,
        beforeId: reset ? undefined : txs[txs.length - 1]?.id,
      })
      txs = reset ? page.transmissions : [...txs, ...page.transmissions]
      txTotal = page.total
    } catch (e) {
      txError = errMsg(e)
    } finally {
      txLoading = false
    }
  }

  async function loadSoundboard(): Promise<void> {
    try {
      const cfg = await getSoundboard()
      clips = cfg.clips
      engines = cfg.engines
      if (!engines.includes(engine)) engine = engines[0] ?? 'espeak'
    } catch {
      /* leave the defaults; the transmit call surfaces any real error */
    }
  }

  async function doTransmit(
    body:
      | { clip: string; settle_ms?: number }
      | { text: string; engine?: string; rate?: number; settle_ms?: number },
  ): Promise<void> {
    if (sending) return
    sending = true
    try {
      await transmitRadio(body)
      toaster.toast('Transmitted')
      loadTxs(true) // the new is_tx row shows at the top of the log
    } catch (e) {
      toaster.toast(errMsg(e), true)
    } finally {
      sending = false
    }
  }

  function transmitText(): void {
    if (ttsText.trim())
      doTransmit({ text: ttsText, engine, rate: ratePct / 100, settle_ms: settleMs })
  }
  const transmitClip = (filename: string): Promise<void> =>
    doTransmit({ clip: filename, settle_ms: settleMs })

  const bandBody = (mhz: number, mode: string, toneHz: number) => ({
    freq_hz: Math.round(mhz * 1e6),
    mode,
    tone: toneHz > 0 ? { mode: 'tone', hz: toneHz } : { mode: 'off' },
  })
  async function stage(body: unknown, ok: string): Promise<void> {
    if (xbStaging) return
    xbStaging = true
    try {
      await postRadio('/api/radio/stage_crossband', body)
      await poll()
      toaster.toast(ok)
    } catch (e) {
      toaster.toast(errMsg(e), true)
    } finally {
      xbStaging = false
    }
  }
  const stageCrossband = (): Promise<void> =>
    stage(
      {
        a: bandBody(xb.aFreq, xb.aMode, xb.aTone),
        b: bandBody(xb.bFreq, xb.bMode, xb.bTone),
        rfpower: xb.power,
        main: xb.main,
      },
      'Staged — engage Repeater Mode on the rig',
    )
  const stageRaceNet = (): Promise<void> =>
    stage(RACE_PRESET, 'Race net staged — engage Repeater Mode on the rig')

  async function sendPower(on: boolean): Promise<void> {
    if (powering) return
    powering = true
    try {
      await postRadio('/api/radio/power', { on })
      toaster.toast(on ? 'Power-on sent' : 'Power-off sent')
      if (on) setTimeout(poll, 3000) // let the rig wake, then refresh the readout
    } catch (e) {
      toaster.toast(errMsg(e), true)
    } finally {
      powering = false
    }
  }
  function powerOn(): void {
    powerConfirm = false
    if (powerConfirmTimer) clearTimeout(powerConfirmTimer)
    sendPower(true)
  }
  function powerOff(): void {
    if (powerConfirm) {
      powerConfirm = false
      if (powerConfirmTimer) clearTimeout(powerConfirmTimer)
      sendPower(false)
    } else {
      powerConfirm = true
      powerConfirmTimer = window.setTimeout(() => (powerConfirm = false), 4000)
    }
  }

  function onBlipToggle(): void {
    expandedId = null
    loadTxs(true)
  }

  const toggleTx = (id: number): void => {
    expandedId = expandedId === id ? null : id
  }

  const txTime = (iso: string): string =>
    new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  const fmtDb = (v: number | null): string => (v == null ? '—' : `${v.toFixed(1)} dBFS`)

  function onToneFreqChange(): void {
    if (s?.tone_mode && s.tone_mode !== 'off') applyTone(s.tone_mode)
  }
  function applyCurrentShift(): void {
    const sh = s?.rptr_shift && s.rptr_shift !== 'simplex' ? s.rptr_shift : 'plus'
    applyShift(sh)
  }

  onMount(() => {
    poll()
    loadTxs(true)
    loadSoundboard()
    pollTimer = window.setInterval(poll, 2000)
  })
  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
    if (powerConfirmTimer) clearTimeout(powerConfirmTimer)
    stopSession()
  })

  const freqMhz = (hz: number | undefined): string => (hz == null ? '—' : (hz / 1e6).toFixed(3))
  const smeter = $derived(s?.online ? sMeter(s.rawstr) : null)
</script>

<header class="page-head">
  <h1>Radio — Icom ID-5100A</h1>
  <p class="muted">Main band · CI-V control</p>
</header>

{#if !s}
  <div class="status-banner err">Connecting…</div>
{:else if !s.online}
  <div class="status-banner err">
    Radio offline — rigctld {s.service ?? 'unreachable'}. Connect the CI-V cable and enable the
    radio-control service.
  </div>
{:else}
  <div class="status-banner ok">● Connected</div>
{/if}

<div class="card">
  <div class="readout">
    <div class="freq">{freqMhz(s?.online ? s.freq_hz : undefined)}<span class="unit">MHz</span></div>
    <div class="mode-badge">{s?.online ? (s.mode ?? '—') : '—'}</div>
  </div>
  <div class="flags">
    <div class="flag rx" class:on={s?.online && s.dcd}><span class="dot"></span>RX (squelch)</div>
    <div class="flag tx" class:on={s?.online && s.ptt}><span class="dot"></span>TX</div>
  </div>
  <div class="smeter">
    <div class="track">
      <div class="fill" style="width:{smeter?.pct ?? 0}%"></div>
      <div class="s9-tick" style="left:{(RAWSTR_S9 / 255) * 100}%"></div>
    </div>
    <div class="smeter-label">
      <span>S-meter</span>
      <span>{smeter ? `${smeter.label} (${Math.round(s!.rawstr!)})` : '—'}</span>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">Listen live</div>
  <div class="listen-row">
    <button
      class="primary listen-btn"
      class:live={listenState === 'live'}
      onclick={toggleListen}
      disabled={listenState === 'connecting'}
    >
      {#if listenState === 'live'}■ Stop{:else if listenState === 'connecting'}Connecting…{:else}▶ Listen{/if}
    </button>
    <div class="listen-status">
      {#if listenState === 'live'}
        <span class="live-dot"></span>Live{sending ? ' · muted (TX)' : ''}
      {:else if listenState === 'error'}
        <span class="err-text">{listenErr}</span>
      {:else}
        Off
      {/if}
    </div>
  </div>
  {#if listenState === 'live' || listenState === 'connecting'}
    <div class="sub-label">Monitor volume — {listenVol}%</div>
    <input type="range" min="0" max="100" bind:value={listenVol} />
  {/if}
  <!-- svelte-ignore a11y_media_has_caption -->
  <audio bind:this={audioEl} class="hidden-audio"></audio>
  <div class="note">
    Streams the received audio (SP1 A+B mix) over WebRTC from the van's media hub. Auto-mutes while
    you transmit.
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">Transmit</div>
  {#if sending}
    <div class="on-air"><span class="dot"></span>ON AIR — transmitting…</div>
  {/if}
  <textarea
    class="tts"
    rows="2"
    placeholder="Type a message to speak — include KC3HEU"
    bind:value={ttsText}
    disabled={sending}
  ></textarea>
  <div class="tx-send">
    {#if engines.length > 1}
      <div class="seg voice">
        {#each engines as e (e)}
          <button class:active={engine === e} disabled={sending} onclick={() => (engine = e)}>
            {e === 'espeak' ? 'Robotic' : 'Natural'}
          </button>
        {/each}
      </div>
    {/if}
    <button class="primary say" disabled={sending || !ttsText.trim()} onclick={transmitText}>
      Speak &amp; transmit
    </button>
  </div>
  <div class="tx-tune">
    <div class="sub-label">Speech rate — {ratePct}% <span class="hint">· text-to-speech</span></div>
    <input type="range" min="30" max="150" step="10" bind:value={ratePct} disabled={sending} />
    <div class="sub-label">Key-up delay — {settleMs} ms</div>
    <input type="range" min="0" max="1000" step="50" bind:value={settleMs} disabled={sending} />
  </div>
  {#if clips.length}
    <div class="sub-label">Soundboard</div>
    <div class="board">
      {#each clips as c (c.filename)}
        <button class="pad" disabled={sending} onclick={() => transmitClip(c.filename)}>
          {c.label}
        </button>
      {/each}
    </div>
  {/if}
  <div class="note">
    Each press keys the rig and transmits on the active band. Station ID is your responsibility —
    include KC3HEU in the message or clip (§97.119).
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">Transmissions{txTotal ? ` — ${txTotal}` : ''}</div>
  <div class="tx-controls">
    <label class="tx-toggle">
      <input type="checkbox" bind:checked={hideBlips} onchange={onBlipToggle} />
      Hide blips (&lt;{BLIP_S}s)
    </label>
    <button onclick={() => loadTxs(true)} disabled={txLoading}>Refresh</button>
  </div>
  {#if txError}
    <div class="note">Could not load the log — {txError}</div>
  {:else if txs.length === 0}
    <div class="note">{txLoading ? 'Loading…' : 'No captures yet.'}</div>
  {/if}
  <div class="tx-list">
    {#each txs as t (t.id)}
      <div class="tx-item">
        <button class="tx-row" class:open={expandedId === t.id} onclick={() => toggleTx(t.id)}>
          <span class="tx-line">
            {#if t.is_tx === 1}<span class="tx-badge">TX</span>{/if}
            <span class="tx-time">{txTime(t.started_utc)}</span>
            <span class="tx-dur">{t.duration_s.toFixed(1)}s</span>
            <span class="tx-tag" class:unconfirmed={t.dcd_main !== 1}>
              {freqMhz(t.freq_hz ?? undefined)}
              {t.mode ?? ''}{t.dcd_main !== 1 ? ' ?' : ''}
            </span>
            {#if !t.has_audio}<span class="tx-pruned">no audio</span>{/if}
          </span>
          {#if t.waveform && expandedId !== t.id}
            <span class="tx-strip"><WaveformStrip samples={parseWaveform(t.waveform)} height={20} /></span>
          {/if}
        </button>
        {#if expandedId === t.id}
          <div class="tx-detail">
            {#if t.has_audio}
              <WaveformPlayer
                id={t.id}
                durationS={t.duration_s}
                samples={parseWaveform(t.waveform)}
              />
            {:else}
              <div class="note">Audio pruned by retention — metadata only.</div>
            {/if}
            <div class="tx-meta">
              <span>Peak {fmtDb(t.peak_dbfs)}</span>
              <span>RMS {fmtDb(t.rms_dbfs)}</span>
              <span>Main squelch {t.dcd_main === 1 ? 'open — tag confirmed' : 'closed — tag unconfirmed'}</span>
              <span
                >{t.lat != null && t.lon != null
                  ? `${t.lat.toFixed(5)}, ${t.lon.toFixed(5)}`
                  : 'no GPS fix'}</span
              >
            </div>
          </div>
        {/if}
      </div>
    {/each}
  </div>
  {#if txs.length < txTotal}
    <button class="tx-more" onclick={() => loadTxs(false)} disabled={txLoading}>
      {txLoading ? 'Loading…' : `Load more (${txTotal - txs.length} older)`}
    </button>
  {/if}
  <div class="note">
    Freq/mode tag the main band at capture start; ? = the main squelch was closed, so the audio
    may be sub-band traffic or local noise.
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">Tune</div>
  <div class="field">
    <input
      type="number"
      step="0.001"
      min="0"
      inputmode="decimal"
      placeholder={s?.online ? `${freqMhz(s.freq_hz)} MHz` : 'frequency (MHz)'}
      bind:value={freqInput}
      onkeydown={(e) => e.key === 'Enter' && submitFreq()}
    />
    <button class="primary" onclick={submitFreq}>Set</button>
  </div>
  <div class="sub-label">Mode</div>
  <div class="seg">
    {#each MODES as m (m.v)}
      <button class:active={s?.online && s.mode === m.v} onclick={() => setMode(m.v)}>{m.l}</button>
    {/each}
  </div>
  <div class="sub-label">Band</div>
  <div class="seg">
    <button onclick={() => setBand('a')}>A → main</button>
    <button onclick={() => setBand('b')}>B → main</button>
  </div>
  <div class="note">
    The rig can't report which band is active, so pin it here before tuning — the radio's
    touchscreen can change it silently.
  </div>
  <div class="sub-label">Watch mode</div>
  <div class="seg">
    <button
      class:active={s?.online && s.dualwatch === false}
      onclick={() => setDualwatch(false)}
    >
      Single
    </button>
    <button class:active={s?.online && s.dualwatch === true} onclick={() => setDualwatch(true)}>
      Dualwatch
    </button>
  </div>
  <div class="note">
    Single watch mutes the sub band everywhere — including the recorder's feed, so sub-band
    noise can't trigger captures.
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">Levels</div>
  <div class="sub-label">Volume — {afPct}%</div>
  <input
    type="range"
    min="0"
    max="100"
    bind:value={afPct}
    onpointerdown={() => (editingAf = true)}
    onpointerup={() => (editingAf = false)}
    onchange={() => setLevel('af', afPct / 100)}
  />
  <div class="sub-label">Squelch — {sqlPct}%</div>
  <input
    type="range"
    min="0"
    max="100"
    bind:value={sqlPct}
    onpointerdown={() => (editingSql = true)}
    onpointerup={() => (editingSql = false)}
    onchange={() => setLevel('sql', sqlPct / 100)}
  />
  <div class="sub-label">TX power</div>
  <div class="seg">
    {#each RF_POWERS as p (p.l)}
      <button class:active={rfActive(p.v)} onclick={() => setLevel('rfpower', p.v)}>{p.l}</button>
    {/each}
  </div>
</div>

<div class="card">
  <div class="card-title eyebrow">CTCSS tone</div>
  <div class="seg">
    {#each TONE_MODES as t (t.v)}
      <button class:active={s?.online && s.tone_mode === t.v} onclick={() => applyTone(t.v)}>
        {t.l}
      </button>
    {/each}
  </div>
  <div class="sub-label">Tone frequency</div>
  <select
    bind:value={ctcss}
    onfocus={() => (editingTone = true)}
    onblur={() => (editingTone = false)}
    onchange={onToneFreqChange}
  >
    {#each CTCSS_TONES as hz (hz)}
      <option value={hz}>{hz.toFixed(1)} Hz</option>
    {/each}
  </select>
  <div class="note">Tone is sent to the rig when you pick a mode or change the frequency.</div>
</div>

<div class="card">
  <div class="card-title eyebrow">Repeater shift</div>
  <div class="seg">
    {#each SHIFTS as sh (sh.v)}
      <button class:active={s?.online && s.rptr_shift === sh.v} onclick={() => applyShift(sh.v)}>
        {sh.l}
      </button>
    {/each}
  </div>
  <div class="sub-label">Offset (kHz)</div>
  <div class="row2">
    <input
      type="number"
      step="5"
      min="0"
      inputmode="numeric"
      bind:value={offsetKhz}
      onfocus={() => (editingOffset = true)}
      onblur={() => (editingOffset = false)}
    />
    <button onclick={applyCurrentShift}>Apply shift</button>
  </div>
  <div class="note">2 m repeaters use 600 kHz; 70 cm uses 5000 kHz.</div>
</div>

<div class="card">
  <button class="xb-head" onclick={() => (xbShow = !xbShow)}>
    <span class="card-title eyebrow">Cross-band repeater</span>
    <span class="xb-chevron" class:open={xbShow}>▸</span>
  </button>
  {#if xbShow}
    <button class="primary xb-preset" disabled={xbStaging} onclick={stageRaceNet}>
      {xbStaging ? 'Staging…' : 'Stage race net'}
    </button>
    <div class="note xb-preset-note">
      147.555 (2m net) ↔ 446.175 (70cm HT link) · TSQL {RACE_PRESET.b.tone.hz} Hz on 70cm · dualwatch
      on. Then engage Repeater Mode on the rig's touchscreen.
    </div>
    <div class="xb-divider">or configure manually</div>
    <div class="xb-bands">
      <div class="xb-band">
        <div class="sub-label">Band A · {xb.main === 'a' ? 'main' : 'sub'}</div>
        <input
          type="number"
          step="0.001"
          min="0"
          inputmode="decimal"
          aria-label="Band A frequency (MHz)"
          bind:value={xb.aFreq}
        />
        <select bind:value={xb.aMode} aria-label="Band A mode">
          {#each MODES as m (m.v)}<option value={m.v}>{m.l}</option>{/each}
        </select>
        <select bind:value={xb.aTone} aria-label="Band A tone">
          <option value={0}>No tone</option>
          {#each CTCSS_TONES as hz (hz)}<option value={hz}>{hz.toFixed(1)} Hz</option>{/each}
        </select>
      </div>
      <div class="xb-band">
        <div class="sub-label">Band B · {xb.main === 'b' ? 'main' : 'sub'}</div>
        <input
          type="number"
          step="0.001"
          min="0"
          inputmode="decimal"
          aria-label="Band B frequency (MHz)"
          bind:value={xb.bFreq}
        />
        <select bind:value={xb.bMode} aria-label="Band B mode">
          {#each MODES as m (m.v)}<option value={m.v}>{m.l}</option>{/each}
        </select>
        <select bind:value={xb.bTone} aria-label="Band B tone">
          <option value={0}>No tone</option>
          {#each CTCSS_TONES as hz (hz)}<option value={hz}>{hz.toFixed(1)} Hz</option>{/each}
        </select>
      </div>
    </div>
    <div class="sub-label">TX power</div>
    <div class="seg">
      {#each RF_POWERS as p (p.l)}
        <button class:active={Math.abs(xb.power - p.v) < 0.05} onclick={() => (xb.power = p.v)}>
          {p.l}
        </button>
      {/each}
    </div>
    <div class="sub-label">Leave active (Main)</div>
    <div class="seg">
      <button class:active={xb.main === 'a'} onclick={() => (xb.main = 'a')}>Band A</button>
      <button class:active={xb.main === 'b'} onclick={() => (xb.main = 'b')}>Band B</button>
    </div>
    <button class="primary xb-stage" disabled={xbStaging} onclick={stageCrossband}>
      {xbStaging ? 'Staging…' : 'Stage cross-band'}
    </button>
    <div class="note">
      Sets both bands' freq/mode/tone and TX power, then turns dualwatch on. Repeater Mode itself
      isn't remote-controllable — engage it on the rig's touchscreen afterward. You're the control
      operator: cross-band retransmission still needs your station ID (KC3HEU).
    </div>
  {/if}
</div>

<div class="card">
  <div class="card-title eyebrow">Rig power</div>
  <div class="seg">
    <button onclick={powerOn} disabled={powering}>Turn on</button>
    <button class="danger" class:armed={powerConfirm} onclick={powerOff} disabled={powering}>
      {powerConfirm ? 'Confirm off' : 'Turn off'}
    </button>
  </div>
  <div class="note">
    Remote power over CI-V (bare <code>18 00</code> off · 25× <code>FE</code> wakeup + <code
      >18 01</code
    > on). The rig can't report its power state, so this is fire-and-forget. After a power-on it
    resets TX power and reverts squelch — re-check the Levels card (the recorder re-asserts squelch
    within about a minute).
  </div>
</div>

<Toast c={toaster} />

<style>
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 14px;
  }
  .card-title {
    margin-bottom: 12px;
  }

  .readout {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .freq {
    font-size: 40px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .freq .unit {
    font-size: 18px;
    color: var(--text-dim);
    font-weight: 500;
    margin-left: 6px;
  }
  .mode-badge {
    font-size: 14px;
    font-weight: 600;
    color: var(--accent);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
  }

  .flags {
    display: flex;
    gap: 10px;
    margin-top: 12px;
  }
  .flag {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .flag .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--border);
  }
  .flag.on.rx .dot {
    background: var(--ok);
  }
  .flag.on.tx .dot {
    background: var(--err);
  }

  .smeter {
    margin-top: 14px;
  }
  .track {
    position: relative;
    height: 12px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .s9-tick {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--text-dim);
    opacity: 0.6;
  }
  .fill {
    height: 100%;
    background: linear-gradient(90deg, #22c55e, #f59e0b, #ef4444);
    transition: width 0.25s;
  }
  .smeter-label {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 4px;
  }

  .field {
    display: flex;
    gap: 8px;
    margin-bottom: 4px;
  }
  input {
    flex: 1;
    width: 100%;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 9px 11px;
    font: inherit;
    font-size: 16px;
    font-variant-numeric: tabular-nums;
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
  }
  input[type='range'] {
    padding: 0;
    height: 28px;
    accent-color: var(--accent);
    border: none;
    background: none;
  }
  button {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 9px 12px;
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #06121f;
    font-weight: 600;
  }
  .seg {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .seg button {
    flex: 1;
    min-width: 60px;
  }
  .seg button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #06121f;
    font-weight: 600;
  }
  select {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 9px 11px;
    font: inherit;
    font-size: 15px;
    width: 100%;
  }
  .sub-label {
    font-size: 12px;
    color: var(--text-dim);
    margin: 12px 0 6px;
  }
  .row2 {
    display: flex;
    gap: 8px;
  }
  .row2 > * {
    flex: 1;
  }
  .note {
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 6px;
  }

  .tx-controls {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 10px;
  }
  .tx-toggle {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 13px;
    color: var(--text-dim);
    cursor: pointer;
  }
  .tx-toggle input {
    flex: none;
    width: 16px;
    height: 16px;
    accent-color: var(--accent);
    margin: 0;
  }
  .tx-controls button {
    flex: none;
  }
  .tx-list {
    display: flex;
    flex-direction: column;
  }
  .tx-item + .tx-item {
    border-top: 1px solid var(--border);
  }
  .tx-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
    background: none;
    border: none;
    border-radius: 0;
    padding: 10px 2px;
    text-align: left;
    font-variant-numeric: tabular-nums;
  }
  .tx-line {
    display: flex;
    align-items: baseline;
    gap: 10px;
    width: 100%;
  }
  .tx-strip {
    display: block;
    width: 100%;
    opacity: 0.85;
  }
  .tx-row.open {
    color: var(--accent);
  }
  .tx-time {
    font-size: 13px;
    white-space: nowrap;
  }
  .tx-dur {
    font-size: 13px;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .tx-tag {
    font-size: 13px;
    margin-left: auto;
    white-space: nowrap;
  }
  .tx-tag.unconfirmed {
    color: var(--text-dim);
  }
  .tx-pruned {
    font-size: 11px;
    color: var(--text-dim);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    white-space: nowrap;
  }
  .tx-detail {
    padding: 2px 2px 12px;
  }
  .tx-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 8px;
  }
  .tx-more {
    width: 100%;
    margin-top: 10px;
  }

  .tx-badge {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--err);
    border: 1px solid var(--err);
    border-radius: 4px;
    padding: 0 4px;
    align-self: center;
  }

  .listen-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .listen-btn {
    flex: none;
    min-width: 130px;
  }
  .listen-btn.live {
    background: var(--err);
    border-color: var(--err);
    color: #fff;
  }
  .listen-status {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 13px;
    color: var(--text-dim);
  }
  .live-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--ok);
    animation: pulse 1.4s ease-in-out infinite;
  }
  .listen-status .err-text {
    color: var(--err);
  }
  .hidden-audio {
    display: none;
  }

  .on-air {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--err);
    background: color-mix(in srgb, var(--err) 12%, transparent);
    border: 1px solid var(--err);
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 10px;
  }
  .on-air .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--err);
    animation: pulse 1s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      opacity: 0.3;
    }
  }

  textarea.tts {
    width: 100%;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 9px 11px;
    font: inherit;
    font-size: 15px;
    resize: vertical;
    margin-bottom: 8px;
  }
  textarea.tts:focus {
    outline: none;
    border-color: var(--accent);
  }
  .tx-send {
    display: flex;
    gap: 8px;
    align-items: stretch;
  }
  .seg.voice {
    flex: none;
  }
  .seg.voice button {
    min-width: 72px;
  }
  .say {
    flex: 1;
  }
  .tx-tune {
    margin-top: 4px;
    margin-bottom: 4px;
  }
  .tx-tune .hint {
    color: var(--text-dim);
    font-weight: 400;
  }
  .board {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 8px;
  }

  .xb-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
  }
  .xb-head .card-title {
    margin-bottom: 0;
  }
  .xb-chevron {
    color: var(--text-dim);
    transition: transform 0.15s;
  }
  .xb-chevron.open {
    transform: rotate(90deg);
  }
  .xb-bands {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
    margin-top: 12px;
  }
  .xb-band {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .xb-stage {
    width: 100%;
    margin-top: 12px;
  }
  .xb-preset {
    width: 100%;
    margin-top: 4px;
  }
  .xb-preset-note {
    margin-bottom: 4px;
  }
  .xb-divider {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 14px 0 4px;
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .xb-divider::before,
  .xb-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  button.danger {
    border-color: color-mix(in srgb, var(--err) 55%, var(--border));
    color: var(--err);
  }
  button.danger.armed {
    background: var(--err);
    border-color: var(--err);
    color: #fff;
    font-weight: 600;
  }
  .note code {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    background: var(--surface-2);
    border-radius: 4px;
    padding: 0 3px;
  }
  .pad {
    padding: 14px 10px;
    font-weight: 600;
    text-align: center;
    overflow-wrap: anywhere;
  }
  .pad:disabled {
    opacity: 0.5;
  }
</style>
