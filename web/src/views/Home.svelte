<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { errMsg } from '../lib/errors'

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
      error = errMsg(e)
    }
  }

  onMount(() => {
    refresh()
    timer = window.setInterval(refresh, 5000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })

  // Per-domain freshness windows (ms): how old a reading can be before the panel
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

  interface Stat {
    label: string
    value: string
    // Small secondary readout under the value (e.g. the °F of a °C temp).
    alt?: string
    warn?: boolean
  }

  interface Panel {
    name: string
    headline: string
    // Small secondary readout beside the headline.
    alt?: string
    // Status line under the headline ('no data', a fault, 'stale · 12m').
    note?: string
    dim: boolean
    warn?: boolean
    stats: Stat[]
  }

  // OBD stream faults (from the sensors registry via /api/status). These take
  // precedence over reading staleness: an unplugged cable is not "engine off".
  const OBD_LINK_FAULTS: Record<string, string> = {
    no_adapter: 'OBD adapter unplugged (USB)',
    no_car: 'adapter not in OBD socket',
    offline: 'OBD reader offline',
  }

  // Victron / vcgencmd decode maps — mirrors METRIC_META codes (api/sensor_schema.py).
  const BATTERY_STATE: Record<number, string> = { 0: 'Idle', 1: 'Charging', 2: 'Discharging' }
  const SOLAR_STATE: Record<number, string> = {
    0: 'Off',
    2: 'Fault',
    3: 'Bulk',
    4: 'Absorption',
    5: 'Float',
    6: 'Storage',
    7: 'Equalize',
    245: 'Wake-up',
    252: 'Ext. control',
  }
  const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

  const r0 = (n: number | null | undefined): string =>
    n == null ? '—' : String(Math.round(n))
  const r1 = (n: number | null | undefined): string => (n == null ? '—' : n.toFixed(1))
  const r2 = (n: number | null | undefined): string => (n == null ? '—' : n.toFixed(2))

  /** Format a Celsius value as Fahrenheit, or an em dash when absent. */
  const f0 = (c: number | null | undefined): string =>
    c == null ? '—' : String(Math.round((c * 9) / 5 + 32))
  const f1 = (c: number | null | undefined): string =>
    c == null ? '—' : ((c * 9) / 5 + 32).toFixed(1)

  /** km/h → whole mph. */
  const mph = (kph: number | null | undefined): string =>
    kph == null ? '—' : String(Math.round(kph * 0.621371))

  /** metres → whole feet. */
  const ft = (m: number | null | undefined): string =>
    m == null ? '—' : String(Math.round(m * 3.28084))

  /** Seconds → compact duration ('12d 4h', '4h 23m', '23m'). */
  function dur(s: number | null | undefined): string {
    if (s == null) return '—'
    const d = Math.floor(s / 86400)
    const h = Math.floor((s % 86400) / 3600)
    const m = Math.floor((s % 3600) / 60)
    if (d > 0) return `${d}d ${h}h`
    if (h > 0) return `${h}h ${m}m`
    return `${m}m`
  }

  /** Degrees → compass point ('NW'). */
  const compass = (deg: number | null | undefined): string =>
    deg == null ? '—' : COMPASS[Math.round(deg / 45) % 8]

  function buildPanels(s: Status): Panel[] {
    const age = (ts: string): number => Date.parse(s.now) - Date.parse(ts)
    /** 'stale · 12m' note for a reading older than its freshness window. */
    const staleNote = (ts: string, max: number): string | undefined =>
      age(ts) > max ? `stale · ${dur(age(ts) / 1000)} ago` : undefined
    const empty = (name: string): Panel => ({
      name,
      headline: '—',
      note: 'no data',
      dim: true,
      stats: [],
    })
    const panels: Panel[] = []

    if (!s.house) {
      panels.push(empty('House power'))
    } else {
      panels.push({
        name: 'House power',
        headline: `${r0(s.house.battery_soc)}%`,
        alt: BATTERY_STATE[s.house.battery_state ?? -1] ?? '',
        note: staleNote(s.house.timestamp, MAX_AGE.house),
        dim: age(s.house.timestamp) > MAX_AGE.house,
        stats: [
          {
            label: 'Battery',
            value: `${r0(s.house.battery_power)} W`,
            alt: `${r2(s.house.battery_voltage)} V · ${r1(s.house.battery_current)} A`,
          },
          {
            label: 'Solar',
            value: `${r0(s.house.pv_power)} W`,
            alt: SOLAR_STATE[s.house.solar_state ?? -1],
          },
          { label: 'Yield today', value: `${r2(s.house.pv_yield_today_kwh)} kWh` },
          { label: 'DC load', value: `${r0(s.house.dc_system_power)} W` },
          { label: 'AC load', value: `${r0(s.house.ac_consumption_power)} W` },
          { label: 'Time to go', value: dur(s.house.time_to_go_s) },
        ],
      })
    }

    if (!s.location) {
      panels.push(empty('Location'))
    } else {
      const loc = s.location
      const moving = (loc.speed ?? 0) > 0.5
      const fix = loc.mode === 3 ? '3D fix' : loc.mode === 2 ? '2D fix' : 'no fix'
      panels.push({
        name: 'Location',
        headline: moving ? 'Moving' : 'Parked',
        alt: moving ? `${mph((loc.speed ?? 0) * 3.6)} mph` : '',
        note: staleNote(loc.timestamp, MAX_AGE.location),
        dim: age(loc.timestamp) > MAX_AGE.location,
        stats: [
          { label: 'Fix', value: fix, warn: loc.mode != null && loc.mode < 2 },
          { label: 'Position', value: `${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}` },
          { label: 'Altitude', value: `${r0(loc.altitude)} m`, alt: `${ft(loc.altitude)} ft` },
          ...(moving
            ? [{ label: 'Heading', value: `${compass(loc.track)} ${r0(loc.track)}°` }]
            : []),
        ],
      })
    }

    if (!s.cabin) {
      panels.push(empty('Cabin'))
    } else {
      panels.push({
        name: 'Cabin',
        headline: `${r1(s.cabin.temp_c)}°C`,
        alt: `${f1(s.cabin.temp_c)}°F`,
        note: staleNote(s.cabin.timestamp, MAX_AGE.cabin),
        dim: age(s.cabin.timestamp) > MAX_AGE.cabin,
        stats: [
          { label: 'Humidity', value: `${r0(s.cabin.humidity_pct)}%` },
          {
            label: 'Dew point',
            value: `${r1(s.cabin.dew_point_c)}°C`,
            alt: `${f1(s.cabin.dew_point_c)}°F`,
          },
          { label: 'Pressure', value: `${r1(s.cabin.pressure_hpa)} hPa` },
          { label: 'IAQ', value: r0(s.cabin.iaq) },
          { label: 'CO₂-eq', value: `${r0(s.cabin.co2_equivalent)} ppm` },
        ],
      })
    }

    if (!s.gnss) {
      panels.push(empty('GNSS'))
    } else {
      panels.push({
        name: 'GNSS',
        headline: `${s.gnss.nsat_used ?? '—'}/${s.gnss.nsat_seen ?? '—'}`,
        alt: 'sats used/seen',
        note: staleNote(s.gnss.timestamp, MAX_AGE.gnss),
        dim: age(s.gnss.timestamp) > MAX_AGE.gnss,
        stats: [
          { label: 'HDOP', value: r1(s.gnss.hdop) },
          { label: 'VDOP', value: r1(s.gnss.vdop) },
          { label: 'PDOP', value: r1(s.gnss.pdop) },
        ],
      })
    }

    const obdFault = s.obd_link ? OBD_LINK_FAULTS[s.obd_link] : undefined
    if (obdFault) {
      panels.push({ name: 'Van', headline: '—', note: obdFault, dim: false, warn: true, stats: [] })
    } else if (!s.van) {
      panels.push(empty('Van'))
    } else if (age(s.van.timestamp) > MAX_AGE.van) {
      // Engine off: the OBD stream only flows with the engine running, so the last
      // reading's fuel level is the tank as parked — still worth surfacing.
      panels.push({
        name: 'Van',
        headline: 'Off',
        alt: 'engine',
        dim: false,
        stats: [{ label: 'Fuel (last)', value: `${r0(s.van.fuel_level_pct)}%` }],
      })
    } else {
      panels.push({
        name: 'Van',
        headline: r0(s.van.rpm),
        alt: 'rpm',
        dim: false,
        stats: [
          { label: 'Speed', value: `${mph(s.van.speed_kph)} mph` },
          {
            label: 'Coolant',
            value: `${r0(s.van.coolant_c)}°C`,
            alt: `${f0(s.van.coolant_c)}°F`,
          },
          { label: 'Load', value: `${r0(s.van.engine_load_pct)}%` },
          { label: 'Fuel', value: `${r0(s.van.fuel_level_pct)}%` },
          { label: 'Battery', value: `${r1(s.van.voltage_v)} V` },
          {
            label: 'Ambient',
            value: `${r0(s.van.ambient_air_c)}°C`,
            alt: `${f0(s.van.ambient_air_c)}°F`,
          },
        ],
      })
    }

    if (!s.pi) {
      panels.push(empty('Pi'))
    } else {
      // Live bits only (0xF): the sticky since-boot flags (bits 16+) never clear
      // until reboot, so warning on them latches forever after one boot blip.
      const throttled = ((s.pi.throttled ?? 0) & 0xf) !== 0
      panels.push({
        name: 'Pi',
        headline: `${r1(s.pi.cpu_temp_c)}°C`,
        alt: `${f1(s.pi.cpu_temp_c)}°F`,
        note: throttled
          ? 'throttled — check power/cooling'
          : staleNote(s.pi.timestamp, MAX_AGE.pi),
        dim: age(s.pi.timestamp) > MAX_AGE.pi,
        warn: throttled,
        stats: [
          { label: 'Load (1m)', value: r2(s.pi.load_1m) },
          { label: 'Memory', value: `${r0(s.pi.mem_used_pct)}%` },
          { label: 'Disk (root)', value: `${r0(s.pi.disk_root_pct)}%` },
          {
            label: 'NVMe',
            value: `${r0(s.pi.disk_nvme_pct)}%`,
            alt: `${r0(s.pi.disk_nvme_free_gb)} GB free`,
          },
          { label: 'Uptime', value: dur(s.pi.uptime_s) },
        ],
      })
    }

    if (!s.router) {
      panels.push(empty('Network'))
    } else {
      const rt = s.router
      const wanUp = rt.wan_up === 1
      panels.push({
        name: 'Network',
        headline: wanUp ? 'Online' : 'No WAN',
        alt: wanUp ? `${r0(rt.wan_ping_ms)} ms ping` : '',
        note: staleNote(rt.timestamp, MAX_AGE.router),
        dim: age(rt.timestamp) > MAX_AGE.router,
        warn: !wanUp,
        stats: [
          {
            label: 'WAN traffic',
            value: `↓${r0(rt.wan_rx_kbps)} ↑${r0(rt.wan_tx_kbps)}`,
            alt: 'kbps',
          },
          { label: 'HaLow RSSI', value: `${r0(rt.halow_rssi_dbm)} dBm` },
          {
            label: 'HaLow rate',
            value: `↓${r1(rt.halow_rx_mbps)} ↑${r1(rt.halow_tx_mbps)}`,
            alt: 'Mbps',
          },
          { label: 'Stations', value: r0(rt.halow_stations) },
          {
            label: 'Radio temp',
            value: `${r0(rt.halow_temp_c)}°C`,
            alt: `${f0(rt.halow_temp_c)}°F`,
          },
          { label: 'DHCP leases', value: r0(rt.dhcp_leases) },
          { label: 'Uptime', value: dur(rt.uptime_s) },
        ],
      })
    }

    // Recording fleet: NVR headline + a cameras-online aggregate (never per-cam).
    if (!s.nvr && !s.cameras) {
      panels.push(empty('Recording'))
    } else {
      const camsDown = s.cameras != null && s.cameras.online < s.cameras.total
      const hddFault = s.nvr != null && s.nvr.hdd_ok === 0
      const videoLoss = (s.nvr?.channels_video_loss ?? 0) > 0
      const ts = s.cameras?.timestamp ?? s.nvr?.timestamp ?? ''
      panels.push({
        name: 'Recording',
        headline: s.cameras ? `${s.cameras.online}/${s.cameras.total}` : '—',
        alt: 'cams online',
        note: ts ? staleNote(ts, MAX_AGE.recording) : undefined,
        dim: ts !== '' && age(ts) > MAX_AGE.recording,
        warn: camsDown || hddFault || videoLoss,
        stats: s.nvr
          ? [
              { label: 'HDD', value: hddFault ? 'Fault' : 'OK', warn: hddFault },
              {
                label: 'HDD temp',
                value: `${r0(s.nvr.hdd_temp_c)}°C`,
                alt: `${f0(s.nvr.hdd_temp_c)}°F`,
              },
              {
                label: 'Video loss',
                value: r0(s.nvr.channels_video_loss),
                warn: videoLoss,
              },
              { label: 'NVR CPU', value: `${r0(s.nvr.cpu_pct)}%` },
              { label: 'NVR memory', value: `${r0(s.nvr.mem_used_pct)}%` },
              { label: 'Clock drift', value: `${r1(s.nvr.clock_offset_s)} s` },
            ]
          : [],
      })
    }

    return panels
  }

  function svcClass(state: string): string {
    if (state === 'active') return 'ok'
    if (state === 'failed') return 'err'
    return 'warn'
  }

  const panels = $derived(status ? buildPanels(status) : [])
</script>

<header class="page-head">
  <h1>Van OS</h1>
  <p class="muted">
    {#if error}<span class="err-text">offline — {error}</span>
    {:else if updated}Updated {updated}
    {:else}Loading…{/if}
  </p>
</header>

<div class="panels">
  {#each panels as p (p.name)}
    <section class="panel" class:dim={p.dim}>
      <header class="panel-head">
        <span class="panel-name">{p.name}</span>
        <span class="panel-metric" class:warn-text={p.warn}>
          {p.headline}{#if p.alt}<span class="alt">{p.alt}</span>{/if}
        </span>
      </header>
      {#if p.note}
        <p class="panel-note" class:warn-text={p.warn} class:muted={!p.warn}>{p.note}</p>
      {/if}
      {#if p.stats.length}
        <div class="stats">
          {#each p.stats as st (st.label)}
            <div class="stat">
              <div class="stat-label">{st.label}</div>
              <div class="stat-value" class:warn-text={st.warn}>{st.value}</div>
              {#if st.alt}<div class="stat-alt">{st.alt}</div>{/if}
            </div>
          {/each}
        </div>
      {/if}
    </section>
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
  .panels {
    display: grid;
    /* min(…, 100%) keeps a panel from forcing horizontal scroll on narrow phones. */
    grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
    gap: 12px;
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
  }

  .panel.dim {
    opacity: 0.5;
  }

  .panel-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }

  .panel-name {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
  }

  .panel-metric {
    font-size: 26px;
    font-weight: 600;
    color: var(--text);
  }

  .panel-metric .alt {
    font-size: 13px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 6px;
  }

  .panel-note {
    margin: 4px 0 0;
    font-size: 12px;
    text-align: right;
  }

  .stats {
    margin-top: 14px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
    gap: 12px 16px;
  }

  .stat {
    min-width: 0;
  }

  .stat-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }

  .stat-value {
    margin-top: 2px;
    font-size: 15px;
    font-weight: 500;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .stat-alt {
    font-size: 11px;
    color: var(--text-dim);
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
