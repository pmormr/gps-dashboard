<script lang="ts">
  import { getPlacesNearby, type NearbyPlace } from '../lib/api'
  import {
    CITY_NAME_PX,
    currentTown,
    fmtMiles,
    type Here,
    fmtPopulation,
    labelSize,
    type Mark,
    META_PX,
    NAME_PX,
    newCompassState,
    placeMarks,
    proximityScale,
    REFETCH_METERS,
    tickPx,
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
    here = $bindable(null),
  }: {
    lat: number | null
    lon: number | null
    heading: number | null
    bottomPx: number
    /** The current town (bound out for the HUD's centre readout). */
    here?: Here | null
  } = $props()

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
  // shadowing is not a per-frame job. Placement below is. The current-town
  // readout rides the same recomputed candidate set (hysteresis via hereId).
  let hereId: number | null = null
  $effect(() => {
    const cands = candidates
    if (lat == null || lon == null) {
      marks = []
      here = null
      hereId = null
      return
    }
    const van = { lat, lon }
    const fresh = cands.map((c) => ({
      ...c,
      distance_m: haversineMeters(van.lat, van.lon, c.lat, c.lon),
      bearing_deg: initialBearingDeg(van.lat, van.lon, c.lat, c.lon),
    }))
    marks = selectMarks(fresh, compassState)
    here = currentTown(fresh, hereId)
    hereId = here?.place.id ?? null
  })

  // Per-frame placement off the smooth heading. The label box estimate
  // (lib/compass.ts labelSize) and the painted sizes (the custom properties
  // on the root below) derive from the same constants, so they can't drift.
  const placed = $derived(
    placeMarks(marks, heading, { width: frameWidth, height: frameHeight }, labelSize),
  )
</script>

<div
  class="drive-compass"
  style:bottom={`${bottomPx}px`}
  style:--compass-name={`${NAME_PX}px`}
  style:--compass-city={`${CITY_NAME_PX}px`}
  style:--compass-meta={`${META_PX}px`}
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
      style:--tick={`${tickPx(p.mark.tier)}px`}
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
      <span class="meta"><span class="pop">{fmtPopulation(p.mark.population)}</span> · {fmtMiles(p.mark.distanceM)}</span>
    </div>
  {/each}
</div>
