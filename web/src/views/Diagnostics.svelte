<script lang="ts">
  // Diagnostics — the service/infra health hub. Split out of Systems: the van's
  // plumbing (time sync, GPS receiver, log relay, media hub, offline data), as
  // opposed to the physical subsystems (power/environment/fridge, still on
  // Systems). Tile statuses reuse the already-polled /api/status aggregate.
  import { getStatus, type Status } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import type { HubTile } from '../lib/routes'
  import SectionHub from '../lib/SectionHub.svelte'

  const statusFeed = poll<Status>(getStatus, 10000)
  let status = $derived(statusFeed.data)

  // Service liveness from the status aggregate's service strip (no new poll).
  const svcDot = (name: string): 'ok' | 'warn' | 'err' | undefined => {
    const svc = status?.services?.find((s) => s.name === name)
    if (!svc) return undefined
    return svc.state === 'active' ? 'ok' : svc.state === 'failed' ? 'err' : 'warn'
  }

  const tiles = $derived.by((): HubTile[] => {
    const list: HubTile[] = []

    // Time — chrony sync state.
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

    // GPS — fix mode + sat count.
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

    list.push({ label: 'Logs', to: '/syslog', sub: 'syslog buffer → Graylog', dot: svcDot('syslog-ng') })
    list.push({ label: 'Media', to: '/mediamtx', sub: 'camera + radio stream hub', dot: svcDot('mediamtx') })
    // Offline data — static (its freshness needs a separate /api/data/status read).
    list.push({ label: 'Offline data', to: '/data', sub: 'chunk freshness — ready to go dark?' })

    return list
  })
</script>

<div class="app-page">
  <header class="page-head">
    <h1>Diagnostics</h1>
    <p class="muted">Service &amp; infra health — time · GPS · logs · media · offline data</p>
  </header>

  <SectionHub {tiles} />
</div>
