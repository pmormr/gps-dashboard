<script lang="ts">
  // Systems — the physical-subsystem hub: a compact live glance (house power +
  // cabin environment + fridge) over the launcher tiles (Sensors/Trends/Fridge).
  // The service/infra diagnostics (time/GPS/logs/media/data) split off to their
  // own Diagnostics tab. Glance + tiles reuse the already-polled /api/status +
  // /api/sensors aggregates — no new fetching.
  import { getSensors, getStatus } from '../lib/api'
  import type { SensorsResponse, Status } from '../lib/api'
  import { celsiusToF } from '../lib/geo'
  import { poll } from '../lib/poll.svelte'
  import type { HubTile } from '../lib/routes'
  import SectionHub from '../lib/SectionHub.svelte'
  import StatPanel from '../lib/StatPanel.svelte'
  import { dotClass } from '../lib/sensors'

  const sensorsFeed = poll<SensorsResponse>(getSensors, 30000)
  const statusFeed = poll<Status>(getStatus, 10000)
  let sensors = $derived(sensorsFeed.data)
  let status = $derived(statusFeed.data)

  const r0 = (n: number | null | undefined): string => (n == null ? '—' : String(Math.round(n)))
  const r1 = (n: number | null | undefined): string => (n == null ? '—' : n.toFixed(1))
  const r2 = (n: number | null | undefined): string => (n == null ? '—' : n.toFixed(2))
  const f0 = (c: number | null | undefined): string =>
    c == null ? '—' : String(Math.round(celsiusToF(c)))

  const fridge = $derived(sensors?.sensors.find((s) => s.type === 'fridge'))
  const fridgeC0 = $derived((fridge?.latest?.comp0_temp_c as number | null | undefined) ?? null)
  const fridgeC1 = $derived((fridge?.latest?.comp1_temp_c as number | null | undefined) ?? null)

  const tiles = $derived.by((): HubTile[] => {
    const list: HubTile[] = []

    if (sensors) {
      const classes = sensors.sensors.map((s) => dotClass(s))
      const live = classes.filter((c) => c === 'ok').length
      const worst = classes.includes('err') ? 'err' : classes.includes('warn') ? 'warn' : 'ok'
      list.push({
        label: 'Sensors',
        to: '/sensors',
        sub: `${live}/${sensors.sensors.length} streams live`,
        dot: worst,
      })
    } else {
      list.push({ label: 'Sensors', to: '/sensors', sub: 'live telemetry streams' })
    }

    list.push({ label: 'Trends', to: '/trends', sub: 'graph any channel over time' })

    if (fridge?.latest) {
      const d = dotClass(fridge)
      list.push({
        label: 'Fridge',
        to: '/fridge',
        sub: `${r0(fridgeC0)}° · ${r0(fridgeC1)}°C`,
        dot: d === 'err' ? 'err' : d === 'warn' ? 'warn' : 'ok',
      })
    } else {
      list.push({ label: 'Fridge', to: '/fridge', sub: 'setpoints · power · DC history' })
    }

    return list
  })
</script>

<div class="app-page">
  <header class="page-head">
    <h1>Systems</h1>
    <p class="muted">House power · cabin environment · fridge</p>
  </header>

  <div class="dash-grid glance">
    <StatPanel
      name="House power"
      headline={r0(status?.house?.battery_soc)}
      unit="%"
      statMin="70px"
      stats={[
        {
          label: 'Battery',
          value: `${r0(status?.house?.battery_power)} W`,
          alt: `${r2(status?.house?.battery_voltage)} V`,
        },
        { label: 'Solar', value: `${r0(status?.house?.pv_power)} W` },
        { label: 'DC load', value: `${r0(status?.house?.dc_system_power)} W` },
      ]}
    />

    <StatPanel
      name="Cabin"
      headline={r1(status?.cabin?.temp_c)}
      unit="°C"
      statMin="70px"
      stats={[
        { label: 'Feels', value: `${f0(status?.cabin?.temp_c)}°F` },
        { label: 'Humidity', value: `${r0(status?.cabin?.humidity_pct)}%` },
        { label: 'IAQ', value: r0(status?.cabin?.iaq) },
      ]}
    />

    <StatPanel
      name="Fridge"
      headline={r0(fridgeC0)}
      unit="°C"
      statMin="70px"
      stats={[{ label: 'Zone 2', value: `${r0(fridgeC1)}°C` }]}
    />
  </div>

  <div class="eyebrow explore-label">Explore</div>
  <SectionHub {tiles} />
</div>

<style>
  .app-page {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  /* Compact glance panels — narrower min than the launcher tiles so power/cabin/
     fridge sit three-up on a laptop. The panel/stat recipe lives in StatPanel. */
  .glance {
    --dash-min: 260px;
  }
  .explore-label {
    margin-top: 4px;
  }
</style>
