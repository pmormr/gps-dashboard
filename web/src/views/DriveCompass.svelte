<script lang="ts">
  import { getPlacesNearby, type NearbyPlace } from '../lib/api'
  import {
    fmtMiles,
    type Mark,
    newCompassState,
    packLabels,
    pips,
    REFETCH_METERS,
    REFETCH_MS,
    relBearing,
    requestRadiiKm,
    selectMarks,
    stripX,
  } from '../lib/compass'
  import { haversineMeters, initialBearingDeg } from '../lib/geo'

  // The town compass: two bearing strips framing the map — ahead on top,
  // behind above the HUD — each mark a settlement from /api/places/nearby
  // weighted by population tier (lib/compass.ts owns the policy). Candidates
  // refetch on movement/time; distance + bearing are recomputed client-side
  // from the raw fix every selection, since the server's values age with
  // every metre driven after the fetch.
  let {
    lat,
    lon,
    heading,
    bottomPx,
  }: { lat: number | null; lon: number | null; heading: number | null; bottomPx: number } =
    $props()

  let candidates = $state<NearbyPlace[]>([])
  let marks = $state<Mark[]>([])
  let stripWidth = $state(0)
  const compassState = newCompassState()

  // Fetch bookkeeping (plain lets — per-tick mutation must not retrigger effects).
  let fetchCenter: { lat: number; lon: number } | null = null
  let fetchedAtMs = 0
  let inflight: AbortController | null = null

  /** Heading snapped to 3° so selection (the O(n²) shadowing) isn't a per-frame job. */
  const headingStep = $derived(heading == null ? null : Math.round(heading / 3) * 3)

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

  // Selection: on a new candidate set, a fresh fix, or a 3° heading step.
  $effect(() => {
    const step = headingStep
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
    marks = selectMarks(fresh, compassState, step)
  })

  // Per-frame positions off the smooth heading (selection decided the strip).
  const positioned = $derived(
    marks.map((m) => {
      const rel = relBearing(heading, m.bearing)
      return { ...m, rel, x: stripX(m.strip, rel) }
    }),
  )

  // Label width estimate — the wider of the two stacked lines (name over
  // pips + miles) — avoids DOM measurement per frame. Cities render 14px/800
  // (drive.css), the rest 13px/600–700; the per-char widths track that.
  const CHAR_PX_BY_TIER = [7.2, 7.2, 7.6, 8.8, 8.8]
  const META_PX = 76
  const estimate = (m: Mark): number =>
    Math.max(m.name.length * CHAR_PX_BY_TIER[Math.min(m.tier, 5) - 1], META_PX) + 4

  const ahead = $derived(
    packLabels(
      positioned.filter((m) => m.strip === 'ahead'),
      stripWidth,
      estimate,
    ),
  )
  const behind = $derived(
    packLabels(
      positioned.filter((m) => m.strip === 'behind'),
      stripWidth,
      estimate,
    ),
  )
</script>

<div class="drive-compass drive-compass--ahead" bind:clientWidth={stripWidth}>
  <div class="drive-compass-axis"><span class="drive-compass-nose">▲</span></div>
  {#each ahead as p (p.mark.id)}
    <div
      class="drive-compass-mark"
      class:row1={p.row === 1}
      data-tier={p.mark.tier}
      style:left={`${p.left}px`}
      style:width={`${p.width}px`}
    >
      <span class="tick" style:left={`${p.mark.x * stripWidth - p.left}px`}></span>
      <span class="name">{p.mark.name}</span>
      <span class="meta"><span class="pips">{pips(p.mark.tier)}</span> {fmtMiles(p.mark.distanceM)}</span>
    </div>
  {/each}
</div>

<div class="drive-compass drive-compass--behind" style:bottom={`${bottomPx}px`}>
  {#each behind as p (p.mark.id)}
    <div
      class="drive-compass-mark"
      class:row1={p.row === 1}
      data-tier={p.mark.tier}
      style:left={`${p.left}px`}
      style:width={`${p.width}px`}
    >
      <span class="tick" style:left={`${p.mark.x * stripWidth - p.left}px`}></span>
      <span class="name">{p.mark.name}</span>
      <span class="meta"><span class="pips">{pips(p.mark.tier)}</span> {fmtMiles(p.mark.distanceM)}</span>
    </div>
  {/each}
  <div class="drive-compass-axis"><span class="drive-compass-nose">▼</span></div>
</div>
