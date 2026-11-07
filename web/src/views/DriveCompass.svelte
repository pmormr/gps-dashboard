<script lang="ts">
  import { getPlacesNearby, type NearbyPlace } from '../lib/api'
  import {
    fmtMiles,
    type Mark,
    newCompassState,
    pips,
    placeMarks,
    proximityScale,
    REFETCH_METERS,
    REFETCH_MS,
    requestRadiiKm,
    selectMarks,
  } from '../lib/compass'
  import { haversineMeters, initialBearingDeg } from '../lib/geo'

  // The town compass: a ring of settlement marks around the map's edge, each
  // where the ray from the van at its relative bearing meets the edge
  // (lib/compass.ts owns the policy). Candidates refetch on movement/time;
  // distance + bearing are recomputed client-side from the raw fix every
  // selection, since the server's values age with every metre driven.
  let {
    lat,
    lon,
    heading,
    bottomPx,
  }: { lat: number | null; lon: number | null; heading: number | null; bottomPx: number } =
    $props()

  let candidates = $state<NearbyPlace[]>([])
  let marks = $state<Mark[]>([])
  let frameWidth = $state(0)
  let frameHeight = $state(0)
  const compassState = newCompassState()

  // Fetch bookkeeping (plain lets — per-tick mutation must not retrigger effects).
  let fetchCenter: { lat: number; lon: number } | null = null
  let fetchedAtMs = 0
  let inflight: AbortController | null = null

  function needsFetch(now: number): boolean {
    if (lat == null || lon == null) return false
    if (!fetchCenter) return true
    if (now - fetchedAtMs > REFETCH_MS) return true
    return haversineMeters(lat, lon, fetchCenter.lat, fetchCenter.lon) > REFETCH_METERS
  }

  function refetch(): void {
    if (lat == null || lon == null) return
    inflight?.abort()
    const ctl = new AbortController()
    inflight = ctl
    const center = { lat, lon }
    getPlacesNearby(lat, lon, requestRadiiKm(), ctl.signal)
      .then((resp) => {
        if (ctl.signal.aborted) return
        candidates = resp.places
        fetchCenter = center
        fetchedAtMs = performance.now()
      })
      .catch(() => {
        /* keep the last candidate set; the next tick retries */
      })
      .finally(() => {
        if (inflight === ctl) inflight = null
      })
  }

  // Refetch gate: runs on every fresh fix (1 Hz) plus a timer for the parked case.
  $effect(() => {
    void lat
    void lon
    if (!inflight && needsFetch(performance.now())) refetch()
  })
  $effect(() => {
    const timer = setInterval(() => {
      if (!inflight && needsFetch(performance.now())) refetch()
    }, 5000)
    return () => {
      clearInterval(timer)
      inflight?.abort()
    }
  })

  // Selection: on a new candidate set or a fresh fix (1 Hz) — the O(n²)
  // shadowing is not a per-frame job. Placement below is.
  $effect(() => {
    const cands = candidates
    if (lat == null || lon == null) {
      marks = []
      return
    }
    const here = { lat, lon }
    const fresh = cands.map((c) => ({
      ...c,
      distance_m: haversineMeters(here.lat, here.lon, c.lat, c.lon),
      bearing_deg: initialBearingDeg(here.lat, here.lon, c.lat, c.lon),
    }))
    marks = selectMarks(fresh, compassState)
  })

  // Label size estimate — the wider of the two stacked lines (name over
  // pips + miles), scaled with proximity — avoids DOM measurement per frame.
  // Cities render 14px/800 (drive.css), the rest 13px/600–700.
  const CHAR_PX_BY_TIER = [7.2, 7.2, 7.6, 8.8, 8.8]
  const META_PX = 76
  const LINES_PX = 26
  const size = (m: Mark): { width: number; height: number } => {
    const s = proximityScale(m)
    return {
      width: (Math.max(m.name.length * CHAR_PX_BY_TIER[Math.min(m.tier, 5) - 1], META_PX) + 4) * s,
      height: LINES_PX * s,
    }
  }

  // Per-frame placement off the smooth heading.
  const placed = $derived(
    placeMarks(marks, heading, { width: frameWidth, height: frameHeight }, size),
  )
</script>

<div
  class="drive-compass"
  style:bottom={`${bottomPx}px`}
  bind:clientWidth={frameWidth}
  bind:clientHeight={frameHeight}
>
  {#each placed as p (p.mark.id)}
    <span
      class="drive-compass-tick"
      data-tier={p.mark.tier}
      style:left={`${p.tickX}px`}
      style:top={`${p.tickY}px`}
      style:--prox={proximityScale(p.mark)}
    ></span>
    <div
      class="drive-compass-mark"
      class:side={p.edge === 'left' || p.edge === 'right'}
      data-tier={p.mark.tier}
      style:left={`${p.x}px`}
      style:top={`${p.y}px`}
      style:--prox={proximityScale(p.mark)}
    >
      <span class="name">{p.mark.name}</span>
      <span class="meta"><span class="pips">{pips(p.mark.tier)}</span> {fmtMiles(p.mark.distanceM)}</span>
    </div>
  {/each}
</div>
