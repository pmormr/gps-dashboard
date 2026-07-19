<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { errMsg } from '../lib/errors'

  import { getPasses, type PassesResponse, type SatPass } from '../lib/api'
  import { fmtDurationSecs } from '../lib/geo'
  import { router } from '../lib/router.svelte'

  // Drill-in to a ported SPA view: a real href (middle-click/new-tab still work),
  // intercepted into client-side navigation so the shell isn't torn down.
  function spaLink(e: MouseEvent, to: string): void {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    router.navigate(to)
  }

  const HORIZONS = [6, 12, 24, 48]
  const MASKS = [0, 5, 10, 20]

  let hours = $state(12)
  let mask = $state(5)
  let data = $state<PassesResponse | null>(null)
  let error = $state<string | null>(null)
  let updated = $state('')
  let timer: number | undefined

  async function load(): Promise<void> {
    try {
      data = await getPasses(hours, mask)
      error = null
      updated = new Date().toLocaleTimeString()
    } catch (e) {
      error = errMsg(e)
    }
  }
  function setHours(h: number): void {
    hours = h
    load()
  }
  function setMask(m: number): void {
    mask = m
    load()
  }

  onMount(() => {
    load()
    timer = window.setInterval(load, 60000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })

  // gnssid → colour, matching the globe/skyplot palette.
  const GNSS_COLOR: Record<number, string> = {
    0: '#22c55e',
    1: '#94a3b8',
    2: '#f59e0b',
    3: '#ef4444',
    5: '#a78bfa',
    6: '#3b82f6',
  }
  const COMPASS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'] // prettier-ignore

  const compass = (az: number): string => COMPASS[Math.round(az / 22.5) % 16]
  const clock = (u: number): string =>
    new Date(u * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  // Round to the minute (pass countdowns want minute resolution), then format.
  const humanGap = (s: number): string => fmtDurationSecs(Math.round(s / 60) * 60, { padMin: true })

  function whenLabel(p: SatPass): { text: string; cls: string } {
    const now = Date.now() / 1000
    if (p.in_progress) return { text: `up now · sets in ${humanGap(p.set_unix - now)}`, cls: 'up' }
    const until = p.rise_unix - now
    return { text: `rises in ${humanGap(until)}`, cls: until < 1800 ? 'soon' : '' }
  }

  const snrText = (p: SatPass): string => (p.max_snr != null ? `${p.max_snr.toFixed(0)} dB·Hz` : '—')
</script>

<header class="page-head">
  <h1>Sky</h1>
  <p class="muted">
    Satellite passes — predicted from logged orbits{#if error}
      · <span class="err-text">{error}</span>
    {:else if updated}
      · updated {updated}{/if}
  </p>
</header>

<div class="views">
  <a class="view" href="/skyplot" onclick={(e) => spaLink(e, '/skyplot')}>
    <span>Skyplot</span><span class="muted">live hemisphere</span>
  </a>
  <a class="view" href="/globe" onclick={(e) => spaLink(e, '/globe')}>
    <span>Globe</span><span class="muted">3D constellation (PC)</span>
  </a>
</div>

<div class="controls">
  <div class="ctl">
    <span class="ctl-label">Horizon</span>
    <div class="chips">
      {#each HORIZONS as h (h)}
        <button class="chip" class:active={hours === h} onclick={() => setHours(h)}>{h}h</button>
      {/each}
    </div>
  </div>
  <div class="ctl">
    <span class="ctl-label">Mask</span>
    <div class="chips">
      {#each MASKS as m (m)}
        <button class="chip" class:active={mask === m} onclick={() => setMask(m)}>{m}°</button>
      {/each}
    </div>
  </div>
</div>

{#if data}
  <div class="summary">
    <b>{data.passes.length}</b> passes in next <b>{data.horizon_hours}h</b> ·
    <b>{data.counts.fit}</b>/<b>{data.counts.observed}</b> sats fit · mask <b>{data.mask_deg}°</b>
  </div>

  {#if data.passes.length === 0}
    <div class="empty muted">
      {data.counts.fit
        ? 'No passes above the mask in this window. Widen the horizon or lower the mask.'
        : 'No orbits fit yet — a few hours of logged satellite observations are needed first.'}
    </div>
  {:else}
    {#each data.passes as p (`${p.gnssid}-${p.svid}-${p.rise_unix}`)}
      {@const w = whenLabel(p)}
      <div class="pass">
        <div class="pass-head">
          <span class="sat">
            <span class="sw" style="background:{GNSS_COLOR[p.gnssid] ?? '#64748b'}"></span>{p.name}
          </span>
          <span class="sys muted">{p.system}</span>
          <span class="when {w.cls}">{w.text}</span>
        </div>
        <div class="legs">
          <div class="leg">
            <div class="leg-name">Rise</div>
            <div class="leg-time">{clock(p.rise_unix)}</div>
            <div class="leg-sub muted">{compass(p.rise_az)} · {p.rise_az.toFixed(0)}°</div>
          </div>
          <div class="leg peak">
            <div class="leg-name">Peak {p.peak_el.toFixed(0)}°</div>
            <div class="leg-time">{clock(p.peak_unix)}</div>
            <div class="leg-sub muted">{compass(p.peak_az)} · {p.peak_az.toFixed(0)}°</div>
          </div>
          <div class="leg">
            <div class="leg-name">Set</div>
            <div class="leg-time">{clock(p.set_unix)}</div>
            <div class="leg-sub muted">{compass(p.set_az)} · {p.set_az.toFixed(0)}°</div>
          </div>
        </div>
        <div class="pass-foot muted">
          <span>Duration <b>{humanGap(p.duration_s)}</b></span>
          <span>Signal <b>{snrText(p)}</b>{p.used ? ' · used' : ''}</span>
        </div>
      </div>
    {/each}
  {/if}
{:else if !error}
  <p class="muted">Loading…</p>
{/if}

<style>
  .err-text {
    color: var(--err);
  }

  .views {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  .view {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    text-decoration: none;
    color: var(--accent);
  }
  .view .muted {
    font-size: 12px;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 14px;
  }
  .ctl {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .ctl-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
  }
  .chips {
    display: flex;
    gap: 6px;
  }
  .chip {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 4px 10px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .chip.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #06121f;
  }

  .summary {
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 14px;
  }
  .summary b {
    color: var(--text);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .empty {
    text-align: center;
    padding: 40px 20px;
  }

  .pass {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 10px;
    padding: 12px 14px;
  }
  .pass-head {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 10px;
  }
  .sat {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 15px;
    font-weight: 600;
  }
  .sw {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .sys {
    font-size: 12px;
  }
  .when {
    margin-left: auto;
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .when.up {
    color: var(--ok);
  }
  .when.soon {
    color: var(--warn);
  }

  .legs {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    text-align: center;
  }
  .leg {
    background: var(--surface-2);
    border-radius: 6px;
    padding: 7px 4px;
  }
  .leg-name {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }
  .leg-time {
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .leg-sub {
    font-size: 11px;
  }
  .leg.peak .leg-time {
    color: var(--accent);
  }
  .pass-foot {
    display: flex;
    gap: 14px;
    margin-top: 9px;
    font-size: 11px;
  }
  .pass-foot b {
    color: var(--text);
    font-weight: 600;
  }
</style>
