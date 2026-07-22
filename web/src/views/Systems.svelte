<script lang="ts">
  // Systems — the section hub. A launcher of tiles with live status, replacing the
  // old buried card-dump + diagnostics button list. The detailed per-node
  // telemetry now lives in the Sensors view (a tile below). Tile statuses reuse
  // the already-polled /api/status + /api/sensors aggregates — no new fetching.
  import { getSensors, getStatus } from '../lib/api'
  import type { SensorsResponse, Status } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import type { HubTile } from '../lib/routes'
  import SectionHub from '../lib/SectionHub.svelte'
  import { dotClass } from '../lib/sensors'

  const sensorsFeed = poll<SensorsResponse>(getSensors, 30000)
  const statusFeed = poll<Status>(getStatus, 10000)
  let sensors = $derived(sensorsFeed.data)
  let status = $derived(statusFeed.data)

  const r0 = (n: number | null | undefined): string => (n == null ? '—' : String(Math.round(n)))

  function worstDot(classes: string[]): 'ok' | 'warn' | 'err' {
    if (classes.includes('err')) return 'err'
    if (classes.includes('warn')) return 'warn'
    return 'ok'
  }

  const tiles = $derived.by((): HubTile[] => {
    const list: HubTile[] = []

    // Sensors — the detailed telemetry, with a live/total stream count.
    if (sensors) {
      const classes = sensors.sensors.map((s) => dotClass(s))
      const live = classes.filter((c) => c === 'ok').length
      list.push({
        label: 'Sensors',
        to: '/sensors',
        sub: `${live}/${sensors.sensors.length} streams live`,
        dot: worstDot(classes),
      })
    } else {
      list.push({ label: 'Sensors', to: '/sensors', sub: 'live telemetry streams' })
    }

    // Trends — a first-class destination (also its own top-level tab); cross-listed
    // here as the natural drill from a sensor reading.
    list.push({ label: 'Trends', to: '/trends', sub: 'graph any channel over time' })

    // Fridge — from the fridge sensor node's latest reading (no separate poll).
    const fridge = sensors?.sensors.find((s) => s.type === 'fridge')
    if (fridge?.latest) {
      const c0 = fridge.latest.comp0_temp_c as number | null
      const c1 = fridge.latest.comp1_temp_c as number | null
      list.push({
        label: 'Fridge',
        to: '/fridge',
        sub: `${r0(c0)}° · ${r0(c1)}°C`,
        dot: dotClass(fridge) === 'err' ? 'err' : dotClass(fridge) === 'warn' ? 'warn' : 'ok',
      })
    } else {
      list.push({ label: 'Fridge', to: '/fridge', sub: 'setpoints · power · DC history' })
    }

    // Offline data — static (its freshness needs a separate /api/data/status read;
    // deferred to keep the hub to the two aggregates it already polls).
    list.push({ label: 'Offline data', to: '/data', sub: 'chunk freshness — ready to go dark?' })

    // Time — chrony sync state from the status aggregate.
    if (status?.ntp) {
      const synced = status.ntp.synced
      list.push({
        label: 'Time',
        to: '/ntp',
        sub: synced === true ? 'clock synced' : synced === false ? 'not synced' : 'sync unknown',
        dot: synced === true ? 'ok' : synced === false ? 'err' : 'warn',
      })
    } else {
      list.push({ label: 'Time', to: '/ntp', sub: 'time sync / chrony' })
    }

    // GPS — fix mode + sat count from the status aggregate.
    if (status?.location) {
      const mode = status.location.mode
      const fix = mode === 3 ? '3D fix' : mode === 2 ? '2D fix' : 'no fix'
      const used = status.gnss?.nsat_used
      list.push({
        label: 'GPS',
        to: '/gpsd',
        sub: used != null ? `${fix} · ${used}/${status.gnss?.nsat_seen ?? '—'} sats` : fix,
        dot: mode != null && mode >= 2 ? 'ok' : 'warn',
      })
    } else {
      list.push({ label: 'GPS', to: '/gpsd', sub: 'GPS receiver status' })
    }

    // Logs — the syslog-ng relay's liveness from the status aggregate's service
    // strip (no new poll); buffer depth lives on the /syslog drill-in.
    const syslog = status?.services?.find((s) => s.name === 'syslog-ng')
    list.push({
      label: 'Logs',
      to: '/syslog',
      sub: 'syslog buffer → Graylog',
      dot: syslog
        ? syslog.state === 'active'
          ? 'ok'
          : syslog.state === 'failed'
            ? 'err'
            : 'warn'
        : undefined,
    })

    return list
  })
</script>

<header class="page-head">
  <h1>Systems</h1>
  <p class="muted">House power · van · environment · diagnostics</p>
</header>

<SectionHub {tiles} />
