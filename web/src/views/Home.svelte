<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getStatus, type Status } from '../lib/api'

  let status = $state<Status | null>(null)
  let error = $state<string | null>(null)
  let updated = $state('')
  let timer: number | undefined

  async function refresh(): Promise<void> {
    try {
      status = await getStatus()
      error = null
      updated = new Date().toLocaleTimeString()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  onMount(() => {
    refresh()
    timer = window.setInterval(refresh, 5000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })

  // Per-domain freshness windows (ms): how old a reading can be before the card
  // reads as stale. The van's is short — a missing recent reading *is* "engine off".
  // pi/router poll at 30 s, the Dahua fleet at 60 s — windows are ~4 missed polls.
  const MAX_AGE = {
    location: 60e3,
    gnss: 60e3,
    house: 120e3,
    cabin: 300e3,
    van: 30e3,
    pi: 120e3,
    router: 120e3,
    recording: 300e3,
  }

  interface Card {
    name: string
    metric: string
    // Small secondary readout shown beside the metric (e.g. the °F of a °C temp).
    alt?: string
    sub: string
    dim: boolean
    // Renders the sub-line in the warning color (a fault, not just stale data).
    warn?: boolean
  }

  // OBD stream faults (from the sensors registry via /api/status). These take
  // precedence over reading staleness: an unplugged cable is not "engine off".
  const OBD_LINK_FAULTS: Record<string, string> = {
    no_adapter: 'OBD adapter unplugged (USB)',
    no_car: 'adapter not in OBD socket',
    offline: 'OBD reader offline',
  }

  const r0 = (n: number | null | undefined): string =>
    n == null ? '—' : String(Math.round(n))
  const r1 = (n: number | null | undefined): string => (n == null ? '—' : n.toFixed(1))

  /** Format a Celsius value as Fahrenheit, or an em dash when absent. */
  const f0 = (c: number | null | undefined): string =>
    c == null ? '—' : String(Math.round((c * 9) / 5 + 32))
  const f1 = (c: number | null | undefined): string =>
    c == null ? '—' : ((c * 9) / 5 + 32).toFixed(1)

  function buildCards(s: Status): Card[] {
    const age = (ts: string): number => Date.parse(s.now) - Date.parse(ts)
    const cards: Card[] = []

    if (!s.house) {
      cards.push({ name: 'House power', metric: '—', sub: 'no data', dim: true })
    } else {
      cards.push({
        name: 'House power',
        metric: `${r0(s.house.battery_soc)}%`,
        sub: `${r0(s.house.pv_power)} W solar · ${r0(s.house.dc_system_power)} W load`,
        dim: age(s.house.timestamp) > MAX_AGE.house,
      })
    }

    if (!s.location) {
      cards.push({ name: 'Location', metric: '—', sub: 'no fix', dim: true })
    } else {
      const moving = (s.location.speed ?? 0) > 0.5
      const fix = s.location.mode === 3 ? '3D fix' : s.location.mode === 2 ? '2D fix' : 'no fix'
      cards.push({
        name: 'Location',
        metric: moving ? 'Moving' : 'Parked',
        sub: fix,
        dim: age(s.location.timestamp) > MAX_AGE.location,
      })
    }

    if (!s.cabin) {
      cards.push({ name: 'Cabin', metric: '—', sub: 'no data', dim: true })
    } else {
      cards.push({
        name: 'Cabin',
        metric: `${r1(s.cabin.temp_c)}°C`,
        alt: `${f1(s.cabin.temp_c)}°F`,
        sub: `${r0(s.cabin.humidity_pct)}% RH · IAQ ${r0(s.cabin.iaq)}`,
        dim: age(s.cabin.timestamp) > MAX_AGE.cabin,
      })
    }

    if (!s.gnss) {
      cards.push({ name: 'GNSS', metric: '—', sub: 'no data', dim: true })
    } else {
      cards.push({
        name: 'GNSS',
        metric: `${s.gnss.nsat_used ?? '—'}/${s.gnss.nsat_seen ?? '—'}`,
        sub: `sats used/seen · PDOP ${r1(s.gnss.pdop)}`,
        dim: age(s.gnss.timestamp) > MAX_AGE.gnss,
      })
    }

    const obdFault = s.obd_link ? OBD_LINK_FAULTS[s.obd_link] : undefined
    if (obdFault) {
      cards.push({ name: 'Van', metric: '—', sub: obdFault, dim: false, warn: true })
    } else if (!s.van) {
      cards.push({ name: 'Van', metric: '—', sub: 'no OBD', dim: true })
    } else if (age(s.van.timestamp) > MAX_AGE.van) {
      cards.push({ name: 'Van', metric: 'Off', sub: 'engine', dim: false })
    } else {
      cards.push({
        name: 'Van',
        metric: r0(s.van.rpm),
        sub: `rpm · coolant ${r0(s.van.coolant_c)}°C / ${f0(s.van.coolant_c)}°F`,
        dim: false,
      })
    }

    if (!s.pi) {
      cards.push({ name: 'Pi', metric: '—', sub: 'no data', dim: true })
    } else {
      const throttled = (s.pi.throttled ?? 0) !== 0
      cards.push({
        name: 'Pi',
        metric: `${r1(s.pi.cpu_temp_c)}°C`,
        alt: `${f1(s.pi.cpu_temp_c)}°F`,
        sub: throttled
          ? 'throttled — check power/cooling'
          : `load ${r1(s.pi.load_1m)} · mem ${r0(s.pi.mem_used_pct)}% · NVMe ${r0(s.pi.disk_nvme_free_gb)} GB free`,
        dim: age(s.pi.timestamp) > MAX_AGE.pi,
        warn: throttled,
      })
    }

    if (!s.router) {
      cards.push({ name: 'Network', metric: '—', sub: 'no data', dim: true })
    } else {
      const wanUp = s.router.wan_up === 1
      cards.push({
        name: 'Network',
        metric: wanUp ? 'Online' : 'No WAN',
        sub: `ping ${r0(s.router.wan_ping_ms)} ms · HaLow ${r0(s.router.halow_rssi_dbm)} dBm · ${r0(s.router.halow_stations)} sta`,
        dim: age(s.router.timestamp) > MAX_AGE.router,
        warn: !wanUp,
      })
    }

    // Recording fleet: NVR headline + a cameras-online aggregate (never per-cam).
    if (!s.nvr && !s.cameras) {
      cards.push({ name: 'Recording', metric: '—', sub: 'no data', dim: true })
    } else {
      const camsDown = s.cameras != null && s.cameras.online < s.cameras.total
      const hddFault = s.nvr != null && s.nvr.hdd_ok === 0
      const videoLoss = (s.nvr?.channels_video_loss ?? 0) > 0
      const subParts = [
        ...(s.cameras ? ['cams online'] : []),
        ...(s.nvr
          ? [
              hddFault ? 'HDD fault' : `HDD ${r0(s.nvr.hdd_temp_c)}°C`,
              videoLoss ? `${r0(s.nvr.channels_video_loss)} video loss` : 'no video loss',
            ]
          : []),
      ]
      const ts = s.cameras?.timestamp ?? s.nvr?.timestamp ?? ''
      cards.push({
        name: 'Recording',
        metric: s.cameras ? `${s.cameras.online}/${s.cameras.total}` : '—',
        sub: subParts.join(' · '),
        dim: ts !== '' && age(ts) > MAX_AGE.recording,
        warn: camsDown || hddFault || videoLoss,
      })
    }

    return cards
  }

  function svcClass(state: string): string {
    if (state === 'active') return 'ok'
    if (state === 'failed') return 'err'
    return 'warn'
  }

  const cards = $derived(status ? buildCards(status) : [])
</script>

<header class="page-head">
  <h1>Van OS</h1>
  <p class="muted">
    {#if error}<span class="err-text">offline — {error}</span>
    {:else if updated}Updated {updated}
    {:else}Loading…{/if}
  </p>
</header>

<div class="cards">
  {#each cards as c (c.name)}
    <div class="card" class:dim={c.dim}>
      <div class="card-metric">{c.metric}{#if c.alt}<span class="alt">{c.alt}</span>{/if}</div>
      <div class="card-name">{c.name}</div>
      <div class="card-sub" class:muted={!c.warn} class:warn-text={c.warn}>{c.sub}</div>
    </div>
  {/each}
</div>

{#if status}
  <div class="health">
    {#each status.services as svc (svc.name)}
      <span class="pill {svcClass(svc.state)}" title={svc.state}>
        <span class="dot"></span>{svc.name.replace(/^gps-|^sensor-/, '')}
      </span>
    {/each}
    <span
      class="pill {status.ntp?.synced === true ? 'ok' : status.ntp?.synced === false ? 'err' : 'warn'}"
      title="NTP"
    >
      <span class="dot"></span>ntp
    </span>
  </div>
{/if}

<style>
  .card.dim {
    opacity: 0.5;
  }

  .card-metric .alt {
    font-size: 13px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 6px;
  }

  .err-text {
    color: var(--err);
  }

  .warn-text {
    color: var(--warn);
  }

  .health {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 20px;
  }

  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 12px;
    color: var(--text-dim);
  }

  .pill .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-dim);
  }

  .pill.ok .dot {
    background: var(--ok);
  }
  .pill.warn .dot {
    background: var(--warn);
  }
  .pill.err .dot {
    background: var(--err);
  }
</style>
