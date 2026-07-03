<script lang="ts">
  import { getAttractions, getPointsLatest, type Attraction } from '../lib/api'
  import { kindMeta } from '../lib/attractions'
  import { fmtDate, fmtDistance, haversineMeters } from '../lib/geo'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers } from '../lib/stores/layers.svelte'
  import './layers.css'

  // The Nearby rail-panel content: attractions distance-sorted around the live
  // fix (fallback: map center when there's no recent fix). One bbox fetch per
  // open/kind-change; ranking is client-side against the anchor.
  let {
    view,
    onOpen,
  }: { view?: typeof MapViewType; onOpen: (id: number) => void } = $props()

  // ~55 km half-side around the anchor; longitude widened by latitude so the
  // box stays roughly square on the ground.
  const HALF_DEG = 0.5
  const MAX_ROWS = 50

  let anchor = $state<{ lat: number; lon: number; live: boolean } | null>(null)
  let rows = $state<(Attraction & { distance_m: number })[]>([])
  let syncedAt = $state<string | null>(null)
  let status = $state('Loading…')
  let filter = $state('')

  const visible = $derived(
    filter
      ? rows.filter((r) => r.name.toLowerCase().includes(filter.toLowerCase()))
      : rows,
  )

  // Resolve the anchor once per mount: live fix, else map center. A separate
  // effect so the fetch effect below never writes its own dependency.
  $effect(() => {
    let cancelled = false
    ;(async () => {
      let a: { lat: number; lon: number; live: boolean } | null = null
      try {
        const fix = await getPointsLatest()
        if (fix && fix.lat != null && fix.lon != null) a = { lat: fix.lat, lon: fix.lon, live: true }
      } catch {
        /* no fix — fall through to map center */
      }
      if (!a) {
        const c = view?.getCenter()
        a = c ? { ...c, live: false } : null
      }
      if (!cancelled) {
        anchor = a
        if (!a) status = 'No position available yet'
      }
    })()
    return () => {
      cancelled = true
    }
  })

  // Refetch when the anchor lands or the kind filter changes (the Set reassigns).
  $effect(() => {
    const a = anchor
    const kinds = [...layers.attractionKinds]
    if (!a) return
    let cancelled = false
    ;(async () => {
      status = 'Loading…'
      if (!kinds.length) {
        rows = []
        status = 'No kinds selected (Data layers panel)'
        return
      }
      const lonHalf = HALF_DEG / Math.max(0.2, Math.cos((a.lat * Math.PI) / 180))
      const bbox = `${a.lon - lonHalf},${a.lat - HALF_DEG},${a.lon + lonHalf},${a.lat + HALF_DEG}`
      try {
        const resp = await getAttractions({ bbox, kinds, limit: 1000 })
        if (cancelled) return
        rows = resp.attractions
          .filter((r) => r.lat != null && r.lon != null)
          .map((r) => ({ ...r, distance_m: haversineMeters(a.lat, a.lon, r.lat!, r.lon!) }))
          .sort((x, y) => x.distance_m - y.distance_m)
          .slice(0, MAX_ROWS)
        syncedAt = rows[0]?.synced_at ?? null
        status = rows.length ? '' : 'Nothing within ~50 km'
      } catch (err) {
        if (!cancelled) status = `Error: ${err instanceof Error ? err.message : String(err)}`
      }
    })()
    return () => {
      cancelled = true
    }
  })
</script>

<div class="layers-panel nearby-panel">
  {#if anchor}
    <div class="label-hint">
      Around {anchor.live ? 'current position' : 'map center'}
      {#if syncedAt}· data as of {fmtDate(syncedAt)}{/if}
    </div>
  {/if}
  <input class="nearby-filter" type="search" placeholder="Filter by name…" bind:value={filter} />
  {#if status}
    <p class="label-hint">{status}</p>
  {/if}
  <ul class="nearby-list">
    {#each visible as row (row.id)}
      <li>
        <button type="button" class="nearby-row" onclick={() => onOpen(row.id)}>
          <span class="nearby-icon" style:color={kindMeta(row.source_kind).color}
            >{kindMeta(row.source_kind).icon}</span
          >
          <span class="nearby-main">
            <span class="nearby-name">{row.name}</span>
            <span class="nearby-meta">
              {fmtDistance(row.distance_m)} · {kindMeta(row.source_kind).label}
              {#if row.park_code}· {row.park_code.toUpperCase()}{/if}
            </span>
          </span>
        </button>
      </li>
    {/each}
  </ul>
</div>
