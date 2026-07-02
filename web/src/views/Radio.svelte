<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getRadioStatus, postRadio, type RadioStatus } from '../lib/api'
  import { RAWSTR_S9, sMeter } from '../lib/radio'

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

  let toastMsg = $state('')
  let toastErr = $state(false)
  let toastShow = $state(false)
  let pollTimer: number | undefined
  let toastTimer: number | undefined

  function toast(msg: string, err = false): void {
    toastMsg = msg
    toastErr = err
    toastShow = true
    clearTimeout(toastTimer)
    toastTimer = window.setTimeout(() => (toastShow = false), 2200)
  }

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
      toast(e instanceof Error ? e.message : String(e), true)
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
  const setLevel = (level: string, value: number): Promise<void> =>
    write('/api/radio/level', { level, value })
  const rfActive = (v: number): boolean =>
    s?.online === true && s.levels?.rfpower != null && Math.abs(s.levels.rfpower - v) < 0.05

  function onToneFreqChange(): void {
    if (s?.tone_mode && s.tone_mode !== 'off') applyTone(s.tone_mode)
  }
  function applyCurrentShift(): void {
    const sh = s?.rptr_shift && s.rptr_shift !== 'simplex' ? s.rptr_shift : 'plus'
    applyShift(sh)
  }

  onMount(() => {
    poll()
    pollTimer = window.setInterval(poll, 2000)
  })
  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
    if (toastTimer) clearTimeout(toastTimer)
  })

  const freqMhz = (hz: number | undefined): string => (hz == null ? '—' : (hz / 1e6).toFixed(3))
  const smeter = $derived(s?.online ? sMeter(s.rawstr) : null)
</script>

<header class="page-head">
  <h1>Radio — Icom ID-5100A</h1>
  <p class="muted">Main band · CI-V control</p>
</header>

{#if !s}
  <div class="banner err">Connecting…</div>
{:else if !s.online}
  <div class="banner err">
    Radio offline — rigctld {s.service ?? 'unreachable'}. Connect the CI-V cable and enable the
    radio-control service.
  </div>
{:else}
  <div class="banner ok">● Connected</div>
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
  <div class="card-title">Tune</div>
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
</div>

<div class="card">
  <div class="card-title">Levels</div>
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
  <div class="card-title">CTCSS tone</div>
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
  <div class="card-title">Repeater shift</div>
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

<div class="toast" class:show={toastShow} class:err={toastErr}>{toastMsg}</div>

<style>
  .banner {
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 16px;
    font-weight: 600;
  }
  .banner.ok {
    background: rgba(62, 207, 142, 0.12);
    color: var(--ok);
    border: 1px solid rgba(62, 207, 142, 0.4);
  }
  .banner.err {
    background: rgba(239, 83, 80, 0.12);
    color: var(--err);
    border: 1px solid rgba(239, 83, 80, 0.4);
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 14px;
  }
  .card-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
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

  .toast {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: calc(var(--nav-h) + env(safe-area-inset-bottom) + 16px);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    opacity: 0;
    transition: opacity 0.2s;
    pointer-events: none;
    max-width: 90vw;
    z-index: 200;
  }
  .toast.show {
    opacity: 1;
  }
  .toast.err {
    border-color: var(--err);
    color: #fecaca;
  }

  @media (min-width: 768px) {
    .toast {
      bottom: 20px;
    }
  }
</style>
