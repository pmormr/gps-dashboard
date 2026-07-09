<script lang="ts">
  import {
    getPlaces,
    getPlaceEvents,
    getPointsLatest,
    type Place,
    type PlaceEvent,
  } from '../lib/api'
  import { CATEGORY_META, placeMeta } from '../lib/places'
  import { fmtDate, fmtDistance, haversineMeters } from '../lib/geo'
  import { router } from '../lib/router.svelte'
  import { browse } from '../lib/stores/places.svelte'
  import { layers } from '../lib/stores/layers.svelte'
  import './places.css'
  import PlaceDetail from './PlaceDetail.svelte'
  import EventDetail from './EventDetail.svelte'

  // The Places destination — the "where do we go next" browser. Master-
  // detail (email-client) layout: list pane with mode/search/filters, detail pane
  // rendering the shared PlaceDetail / EventDetail. Desktop shows both side
  // by side; mobile shows one at a time (list → detail with a back button). The
  // map keeps only waypoints; this view owns search/nearby/browse.
  // Browse state lives in the `browse` store so the session survives tab switches.

  /** Near-me bbox half-side, degrees latitude (~110 km). */
  const HALF_DEG = 1.0
  /** How far ahead the Events mode looks. */
  const EVENT_HORIZON_DAYS = 30
  const PLACES_LIMIT = 2000
  const EVENTS_LIMIT = 2000
  const QUERY_DEBOUNCE_MS = 300
  /** 1-char prefixes match millions of rows — browse until a real query forms. */
  const MIN_QUERY_CHARS = 2
  /** Browse depth without the minor-places toggle (decision 16). */
  const BROWSE_MAX_RANK = 3

  let anchor = $state<{ lat: number; lon: number } | null>(null)
  let anchorResolved = $state(false)
  let places = $state<(Place & { distance_m: number | null })[]>([])
  let events = $state<PlaceEvent[]>([])
  let status = $state('Loading…')
  let syncedAt = $state<string | null>(null)

  // The search input is local and pushes to the store debounced, so each
  // keystroke doesn't refetch.
  let queryInput = $state(browse.query)
  let debounceTimer: ReturnType<typeof setTimeout> | undefined
  function onQueryInput(): void {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => (browse.query = queryInput.trim()), QUERY_DEBOUNCE_MS)
  }

  // Resolve the live fix once per mount. No fix → Everywhere-only browsing.
  $effect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const fix = await getPointsLatest()
        if (!cancelled && fix && fix.lat != null && fix.lon != null) {
          anchor = { lat: fix.lat, lon: fix.lon }
        }
      } catch {
        /* offline fix gap — Everywhere mode still works */
      }
      if (!cancelled) anchorResolved = true
    })()
    return () => {
      cancelled = true
    }
  })

  function nearBbox(a: { lat: number; lon: number }): string {
    const lonHalf = HALF_DEG / Math.max(0.2, Math.cos((a.lat * Math.PI) / 180))
    return `${a.lon - lonHalf},${a.lat - HALF_DEG},${a.lon + lonHalf},${a.lat + HALF_DEG}`
  }

  function localDate(offsetDays: number): string {
    const d = new Date()
    d.setDate(d.getDate() + offsetDays)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }

  const nearActive = $derived(browse.anchorMode === 'near' && anchor != null)

  // Places fetch: mode/filters/search → one list. Browsing gates at
  // BROWSE_MAX_RANK (unless minor places are toggled on) and, near-me, sorts
  // by distance; searching covers all ranks and keeps the server's
  // match → rank → distance-to-anchor order (decision 9).
  $effect(() => {
    if (browse.mode !== 'places' || !anchorResolved) return
    const categories = [...browse.categories]
    const q = browse.query.length >= MIN_QUERY_CHARS ? browse.query : ''
    const showMinor = browse.showMinor
    const a = nearActive ? anchor : null
    const center = anchor
    let cancelled = false
    ;(async () => {
      status = 'Loading…'
      if (!categories.length) {
        places = []
        status = 'No categories selected'
        return
      }
      try {
        const resp = await getPlaces({
          bbox: a ? nearBbox(a) : undefined,
          // All-on means unfiltered — that also keeps unmapped-category rows.
          categories: categories.length === CATEGORY_META.length ? undefined : categories,
          // Minor places need a bbox: an Everywhere all-ranks read sorts the
          // whole 10.7M-row tier server-side (minutes on the Pi).
          maxRank: q || (showMinor && a) ? undefined : BROWSE_MAX_RANK,
          center: q && center ? `${center.lon},${center.lat}` : undefined,
          q: q || undefined,
          limit: PLACES_LIMIT,
        })
        if (cancelled) return
        let rows = resp.places.map((r) => ({
          ...r,
          distance_m:
            a && r.lat != null && r.lon != null
              ? haversineMeters(a.lat, a.lon, r.lat, r.lon)
              : null,
        }))
        if (a && !q)
          rows = rows.sort((x, y) => (x.distance_m ?? Infinity) - (y.distance_m ?? Infinity))
        places = rows
        syncedAt = rows[0]?.synced_at ?? syncedAt
        status = rows.length
          ? resp.truncated
            ? `${rows.length.toLocaleString()} shown (truncated — narrow the search)`
            : ''
          : 'No matches'
      } catch (err) {
        if (!cancelled) status = `Error: ${err instanceof Error ? err.message : String(err)}`
      }
    })()
    return () => {
      cancelled = true
    }
  })

  // Events fetch: the next EVENT_HORIZON_DAYS, bbox-scoped in Near-me mode. The
  // name search filters client-side (the events endpoint has no q param).
  $effect(() => {
    if (browse.mode !== 'events' || !anchorResolved) return
    const a = nearActive ? anchor : null
    let cancelled = false
    ;(async () => {
      status = 'Loading…'
      try {
        const resp = await getPlaceEvents({
          start: localDate(0),
          end: localDate(EVENT_HORIZON_DAYS),
          bbox: a ? nearBbox(a) : undefined,
          limit: EVENTS_LIMIT,
        })
        if (cancelled) return
        events = resp.events
        syncedAt = resp.events[0]?.synced_at ?? syncedAt
        status = resp.events.length
          ? resp.truncated
            ? `${resp.events.length.toLocaleString()} shown (truncated)`
            : ''
          : 'No events in the next month'
      } catch (err) {
        if (!cancelled) status = `Error: ${err instanceof Error ? err.message : String(err)}`
      }
    })()
    return () => {
      cancelled = true
    }
  })

  const visibleEvents = $derived(
    browse.query
      ? events.filter((e) => e.name.toLowerCase().includes(browse.query.toLowerCase()))
      : events,
  )

  // "Show on map": turn the waypoint layer on, queue the zoom (the map engine may
  // not be initialized yet on a cold visit), and navigate.
  function showOnMap(lat: number, lon: number, zoom: number): void {
    layers.places = true
    layers.pendingZoom = { lat, lon, zoom }
    router.navigate('/map')
  }

  function eventDateLabel(ev: PlaceEvent): string {
    const first = ev.dates[0]
    if (!first) return ''
    const time = first.time_start ? ` ${first.time_start}` : ''
    const more = ev.dates.length > 1 ? ` (+${ev.dates.length - 1})` : ''
    return `${first.date}${time}${more}`
  }
</script>

<div class="places-view" class:detail-open={browse.detailOpen}>
  <div class="places-list-pane">
    <div class="places-controls">
      <div class="places-mode">
        <button
          type="button"
          class:active={browse.mode === 'places'}
          onclick={() => (browse.mode = 'places')}>Places</button>
        <button
          type="button"
          class:active={browse.mode === 'events'}
          onclick={() => (browse.mode = 'events')}>Events</button>
        <span class="places-spacer"></span>
        <button
          type="button"
          class="places-anchor"
          class:active={nearActive}
          disabled={!anchor}
          title={anchor ? 'Toggle near-me filtering' : 'No position fix available'}
          onclick={() => (browse.anchorMode = browse.anchorMode === 'near' ? 'everywhere' : 'near')}
          >📍 Near me</button>
      </div>
      <input
        class="nearby-filter"
        type="search"
        placeholder={browse.mode === 'places'
          ? 'Search places (name, cuisine, brand…)'
          : 'Filter events by name…'}
        bind:value={queryInput}
        oninput={onQueryInput}
      />
      {#if browse.mode === 'places'}
        <div class="places-kind-chips">
          {#each CATEGORY_META as c (c.category)}
            <button
              type="button"
              class="attr-chip attr-chip-toggle"
              class:active={browse.categories.has(c.category)}
              style:--chip-color={c.color}
              onclick={() => browse.toggleCategory(c.category, !browse.categories.has(c.category))}
              >{c.icon} {c.label}</button>
          {/each}
          <button
            type="button"
            class="attr-chip attr-chip-toggle places-minor-toggle"
            class:active={browse.showMinor && nearActive}
            disabled={browse.query.length >= MIN_QUERY_CHARS || !nearActive}
            title={browse.query.length >= MIN_QUERY_CHARS
              ? 'Search always covers all places'
              : nearActive
                ? 'Include minor places (benches, markers, micro furniture)'
                : 'Minor places need Near me (the full set is too big to list everywhere)'}
            onclick={() => (browse.showMinor = !browse.showMinor)}>+ minor places</button>
        </div>
      {/if}
      <div class="label-hint">
        {#if status}{status}{/if}
        {#if syncedAt}
          {#if status}·{/if} data as of {fmtDate(syncedAt)}
        {/if}
      </div>
    </div>

    <ul class="nearby-list places-list">
      {#if browse.mode === 'places'}
        {#each places as row (row.id)}
          <li>
            <button
              type="button"
              class="nearby-row"
              class:selected={browse.selectedPlace === row.id}
              onclick={() => browse.select(row.id)}
            >
              <span class="nearby-icon" style:color={placeMeta(row).color}
                >{placeMeta(row).icon}</span>
              <span class="nearby-main">
                <span class="nearby-name">{row.name}</span>
                <span class="nearby-meta">
                  {#if row.distance_m != null}{fmtDistance(row.distance_m)} ·{/if}
                  {placeMeta(row).label}
                  <!-- RIDB park codes are numeric RecArea ids — meaningless raw, so hidden -->
                  {#if row.park_code && !/^\d+$/.test(row.park_code)}
                    · {row.park_code.toUpperCase()}
                  {/if}
                </span>
                {#if row.summary}<span class="nearby-teaser">{row.summary}</span>{/if}
              </span>
            </button>
          </li>
        {/each}
      {:else}
        {#each visibleEvents as ev (ev.id)}
          <li>
            <button
              type="button"
              class="nearby-row"
              class:selected={browse.selectedEvent === ev.id}
              onclick={() => browse.select(ev.id)}
            >
              <span class="nearby-icon">📅</span>
              <span class="nearby-main">
                <span class="nearby-name">{ev.name}</span>
                <span class="nearby-meta">
                  {eventDateLabel(ev)}
                  {#if ev.park_code}· {ev.park_code.toUpperCase()}{/if}
                  {#if ev.is_free}· free{/if}
                </span>
              </span>
            </button>
          </li>
        {/each}
      {/if}
    </ul>
  </div>

  <div class="places-detail-pane">
    <button type="button" class="places-back" onclick={() => (browse.detailOpen = false)}
      >← Back to list</button>
    {#if browse.mode === 'places' && browse.selectedPlace != null}
      <PlaceDetail id={browse.selectedPlace} onShowMap={showOnMap} />
    {:else if browse.mode === 'events' && browse.selectedEvent != null}
      <EventDetail id={browse.selectedEvent} onShowMap={showOnMap} />
    {:else}
      <p class="attr-muted places-empty">Select a {browse.mode === 'places' ? 'place' : 'event'} to see its details.</p>
    {/if}
  </div>
</div>
