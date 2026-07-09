<script lang="ts">
  import {
    getAttraction,
    getAttractionEvents,
    type Attraction,
    type AttractionDetails,
    type AttractionEvent,
  } from '../lib/api'
  import { kindMeta, stripHtml } from '../lib/attractions'
  import { fmtDate } from '../lib/geo'
  import { router } from '../lib/router.svelte'
  import { destination } from '../lib/stores/destination.svelte'

  // One attraction's full detail — title, the always-on data-age banner, summary,
  // amenities, hours, fees, tour stops with transcripts, and the park's upcoming
  // events. Shared by the map's detail sheet and the Attractions view's detail
  // pane; the container owns chrome (close/back) and scroll, this owns content.
  // "Show/see on map" is delegated so each container decides what that means
  // (the sheet zooms in place; the Attractions view navigates to /map first).
  let {
    id,
    onShowMap,
  }: { id: number; onShowMap?: (lat: number, lon: number, zoom: number) => void } = $props()

  /** Syncs older than this read as a warning, not just a note. */
  const STALE_DAYS = 45
  /** How far ahead the detail looks for the park's events. */
  const EVENT_HORIZON_DAYS = 30

  let row = $state<(Attraction & { details: AttractionDetails }) | null>(null)
  let events = $state<AttractionEvent[]>([])
  let status = $state('Loading…')

  const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

  const meta = $derived(row ? kindMeta(row.source_kind) : null)
  const ageDays = $derived(
    row ? Math.floor((Date.now() - new Date(row.synced_at).getTime()) / 86_400_000) : 0,
  )

  function localDate(offsetDays: number): string {
    const d = new Date()
    d.setDate(d.getDate() + offsetDays)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }

  $effect(() => {
    const wanted = id
    row = null
    events = []
    status = 'Loading…'
    ;(async () => {
      try {
        const r = await getAttraction(wanted)
        if (wanted !== id) return
        row = r
        status = ''
        if (r.park_code) {
          const resp = await getAttractionEvents({
            park: r.park_code,
            start: localDate(0),
            end: localDate(EVENT_HORIZON_DAYS),
            limit: 200,
          })
          if (wanted === id) events = resp.events
        }
      } catch (err) {
        if (wanted === id) status = `Error: ${err instanceof Error ? err.message : String(err)}`
      }
    })()
  })

  // Sets the Drive destination as a value snapshot (name + coords; source ids
  // ride along as provenance only — attraction rows are full-replaced on
  // re-import, so the id must never be dereferenced later) and heads to /drive.
  function navigateHere(): void {
    const r = row!
    destination.set({
      name: r.name,
      lat: r.lat!,
      lon: r.lon!,
      source: r.source,
      sourceId: r.source_id,
    })
    router.navigate('/drive')
  }

  function occurrenceLabel(ev: AttractionEvent): string {
    const first = ev.dates[0]
    if (!first) return ''
    const time = first.time_start ? ` · ${first.time_start}` : ''
    const more = ev.dates.length > 1 ? ` (+${ev.dates.length - 1} more)` : ''
    return `${first.date}${time}${more}`
  }
</script>

{#if status}
  <p class="attr-muted">{status}</p>
{/if}

{#if row}
  <h2 class="attr-title">
    {#if meta}<span style:color={meta.color}>{meta.icon}</span>{/if}
    {row.name}
  </h2>

  <div class="attr-banner" class:stale={ageDays > STALE_DAYS}>
    Data synced {fmtDate(row.synced_at)}
    {#if ageDays > STALE_DAYS}· {ageDays} days old — verify at a visitor center{/if}
  </div>

  <div class="attr-meta-line">
    {meta?.label}
    {#if row.details.org}· {row.details.org}{/if}
    {#if row.details.recAreaName}
      · {row.details.recAreaName}
    {:else if row.park_code && !/^\d+$/.test(row.park_code)}
      · {row.park_code.toUpperCase()}
    {/if}
    {#if row.details.city || row.details.state}
      · {[row.details.city, row.details.state].filter(Boolean).join(', ')}
    {/if}
    {#if onShowMap && row.lat != null && row.lon != null}
      <button type="button" class="attr-link" onclick={() => onShowMap!(row!.lat!, row!.lon!, 12)}
        >Show on map</button>
    {/if}
    {#if row.lat != null && row.lon != null}
      <button type="button" class="attr-link" onclick={navigateHere}>Navigate here</button>
    {/if}
  </div>

  {#if row.summary}
    <p class="attr-summary">{row.summary}</p>
  {/if}

  {#if row.details.amenities?.length}
    <div class="attr-section">
      <h3>Amenities</h3>
      <div class="attr-chips">
        {#each row.details.amenities as a (a)}<span class="attr-chip">{a}</span>{/each}
      </div>
    </div>
  {/if}

  {#if row.details.activities?.length}
    <div class="attr-section">
      <h3>Activities</h3>
      <div class="attr-chips">
        {#each row.details.activities as a (a)}<span class="attr-chip">{a}</span>{/each}
      </div>
    </div>
  {/if}

  {#if row.details.campsites}
    <div class="attr-section">
      <h3>Campsites ({row.details.campsites.count})</h3>
      <ul class="attr-plain-list">
        {#each row.details.campsites.equipment as eq (eq.name)}
          <li>{eq.name}{#if eq.maxLengthFt}&nbsp;— up to {eq.maxLengthFt} ft{/if}</li>
        {/each}
      </ul>
      {#if row.details.stayLimit}<p class="attr-muted">Stay limit: {row.details.stayLimit}</p>{/if}
    </div>
  {/if}

  {#if row.details.operatingHours?.length}
    <div class="attr-section">
      <h3>Hours</h3>
      {#each row.details.operatingHours as oh, i (i)}
        <div class="attr-hours">
          {#if oh.name}<div class="attr-hours-name">{oh.name}</div>{/if}
          {#if oh.standardHours}
            <table>
              <tbody>
                {#each WEEKDAYS as day (day)}
                  {#if oh.standardHours[day]}
                    <tr><td class="attr-day">{day.slice(0, 3)}</td><td>{oh.standardHours[day]}</td></tr>
                  {/if}
                {/each}
              </tbody>
            </table>
          {/if}
          {#if oh.description}<p class="attr-muted">{oh.description}</p>{/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if row.details.fees?.length}
    <div class="attr-section">
      <h3>Fees</h3>
      <ul class="attr-plain-list">
        {#each row.details.fees as fee, i (i)}
          <li><strong>${fee.cost}</strong>{#if fee.title}&nbsp;— {fee.title}{/if}</li>
        {/each}
      </ul>
    </div>
  {/if}

  {#if row.details.stops?.length}
    <div class="attr-section">
      <h3>Tour stops ({row.details.stops.length})</h3>
      <ol class="attr-stops">
        {#each row.details.stops as stop (stop.ordinal)}
          <li>
            <div class="attr-stop-name">
              {stop.assetName}
              {#if onShowMap && stop.lat != null && stop.lon != null}
                <button
                  type="button"
                  class="attr-link"
                  onclick={() => onShowMap!(stop.lat!, stop.lon!, 15)}>map</button>
              {/if}
            </div>
            {#if stop.significance}<p class="attr-muted">{stripHtml(stop.significance)}</p>{/if}
            {#if stop.audioTranscript}
              <details>
                <summary>Transcript</summary>
                <p class="attr-transcript">{stop.audioTranscript}</p>
              </details>
            {/if}
            {#if stop.directionsToNextStop}
              <p class="attr-directions">→ {stripHtml(stop.directionsToNextStop)}</p>
            {/if}
          </li>
        {/each}
      </ol>
    </div>
  {/if}

  {#if row.details.description && row.details.description !== row.summary}
    <div class="attr-section">
      <h3>About</h3>
      <p class="attr-summary">{stripHtml(row.details.description)}</p>
    </div>
  {/if}

  {#if row.details.directions}
    <div class="attr-section">
      <h3>Directions</h3>
      <p class="attr-summary">{stripHtml(row.details.directions)}</p>
    </div>
  {/if}

  {#if row.details.feeText || row.details.reservable || row.details.phone}
    <div class="attr-section">
      <h3>Practical</h3>
      {#if row.details.feeText}<p class="attr-summary">{stripHtml(row.details.feeText)}</p>{/if}
      <p class="attr-muted">
        {#if row.details.reservable}Reservable via recreation.gov (when online){/if}
        {#if row.details.reservable && row.details.phone}·{/if}
        {#if row.details.phone}{row.details.phone}{/if}
      </p>
    </div>
  {/if}

  {#if events.length}
    <div class="attr-section">
      <h3>Events at {row.park_code?.toUpperCase()} (next {EVENT_HORIZON_DAYS} days)</h3>
      <ul class="attr-plain-list">
        {#each events as ev (ev.id)}
          <li class="attr-event">
            <div class="attr-stop-name">{ev.name}</div>
            <div class="attr-muted">
              {occurrenceLabel(ev)}
              {#if ev.location_text}· {ev.location_text}{/if}
              {#if ev.is_free}· free{/if}
              {#if ev.needs_reservation}· reservation required{/if}
            </div>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
{/if}
