<script lang="ts">
  import { getObdEconomy, type ObdEconomy } from '../lib/api'
  import { selection } from '../lib/stores/selection.svelte'

  // "Inspect this window" — derived stats for the current Selection window
  // (distance / speed / fuel / economy / drive-time), generalizing the per-
  // annotation fuel-economy readout (AnnotationsDrawer) to any scrubbed window.
  // Reads /api/obd/economy, which already joins derived OBD fuel with the GPS-track
  // distance. The rail (Map.svelte) mounts this only while its panel is open, so
  // the fetch effect runs only then — the window can be large and it re-runs on
  // the live tick.
  let data = $state<ObdEconomy | null>(null)
  let loading = $state(false)
  let error = $state<string | null>(null)

  $effect(() => {
    const r = selection.range
    const from = r.from.toISOString()
    const to = r.to.toISOString()
    loading = true
    let cancelled = false
    getObdEconomy(from, to)
      .then((d) => {
        if (!cancelled) {
          data = d
          error = null
          loading = false
        }
      })
      .catch((e) => {
        if (!cancelled) {
          error = e instanceof Error ? e.message : String(e)
          loading = false
        }
      })
    return () => {
      cancelled = true
    }
  })

  const KPH_TO_MPH = 0.621371

  const durationMs = $derived(selection.range.to.getTime() - selection.range.from.getTime())
  const hasObd = $derived(!!data && data.n_obd_rows > 0)
  // Average *moving* speed (excludes stops): GPS distance ÷ OBD moving time.
  const avgMph = $derived(
    data && data.moving_s > 0 ? data.distance_mi / (data.moving_s / 3600) : null
  )
  const maxMph = $derived(data?.max_speed_kph != null ? data.max_speed_kph * KPH_TO_MPH : null)

  /** Compact duration, e.g. "2h 15m", "8m 30s", "45s". */
  function fmtSecs(s: number): string {
    const t = Math.round(s)
    const h = Math.floor(t / 3600)
    const m = Math.floor((t % 3600) / 60)
    const sec = t % 60
    if (h) return `${h}h ${m}m`
    if (m) return `${m}m ${sec}s`
    return `${sec}s`
  }
  function num(v: number | null | undefined, dec: number): string {
    return v == null ? '—' : v.toFixed(dec)
  }
</script>

{#if error}
  <p class="msg err">{error}</p>
{:else if loading && !data}
  <p class="msg muted">Loading…</p>
{:else if data}
  <div class="stat-grid">
    <span class="k">Window</span><span class="v">{fmtSecs(durationMs / 1000)}</span>
    <span class="k">Distance</span><span class="v">{num(data.distance_mi, 1)}<u>mi</u></span>
    <span class="k">Avg moving</span><span class="v">{num(avgMph, 0)}<u>mph</u></span>
    <span class="k">Max speed</span><span class="v">{num(maxMph, 0)}<u>mph</u></span>
    <span class="k">Fuel</span><span class="v">{hasObd ? num(data.fuel_gallons, 2) : '—'}<u>gal</u></span>
    <span class="k">Economy</span><span class="v">{num(data.mpg, 1)}<u>mpg</u></span>
    <span class="k">Moving</span><span class="v">{hasObd ? fmtSecs(data.moving_s) : '—'}</span>
    <span class="k">Idle</span><span class="v">{hasObd ? fmtSecs(data.idle_s) : '—'}</span>
  </div>
  {#if !hasObd}
    <p class="msg muted">No engine data here — GPS distance only.</p>
  {/if}
{/if}

<style>
  .stat-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 5px 10px;
    align-items: baseline;
  }
  .k {
    color: var(--text-dim);
    font-size: 12px;
  }
  .v {
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .v u {
    font-weight: 400;
    font-size: 11px;
    color: var(--text-dim);
    margin-left: 2px;
    text-decoration: none;
  }
  .msg {
    font-size: 12px;
    margin: 8px 0 0;
  }
  .msg.err {
    color: var(--err);
  }
  .msg.muted {
    color: var(--text-dim);
  }
</style>
