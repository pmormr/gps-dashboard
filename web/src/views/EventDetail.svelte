<script lang="ts">
  import { getAttractionEvent, type AttractionEvent } from '../lib/api'
  import { stripHtml } from '../lib/attractions'
  import { fmtDate } from '../lib/geo'

  // One event's full detail — description, the complete occurrence list, and the
  // practical lines (location, fees, reservation). Rendered in the Attractions
  // view's detail pane; same data-age banner rule as places.
  let {
    id,
    onShowMap,
  }: { id: number; onShowMap?: (lat: number, lon: number, zoom: number) => void } = $props()

  const STALE_DAYS = 45

  let row = $state<(AttractionEvent & { details: Record<string, unknown> }) | null>(null)
  let status = $state('Loading…')

  const ageDays = $derived(
    row ? Math.floor((Date.now() - new Date(row.synced_at).getTime()) / 86_400_000) : 0,
  )

  $effect(() => {
    const wanted = id
    row = null
    status = 'Loading…'
    ;(async () => {
      try {
        const r = await getAttractionEvent(wanted)
        if (wanted !== id) return
        row = r
        status = ''
      } catch (err) {
        if (wanted === id) status = `Error: ${err instanceof Error ? err.message : String(err)}`
      }
    })()
  })

  /** A details field flattened to display text ('' when absent/empty). */
  function detailText(key: string): string {
    const v = row?.details[key]
    return typeof v === 'string' && v.trim() ? stripHtml(v) : ''
  }
</script>

{#if status}
  <p class="attr-muted">{status}</p>
{/if}

{#if row}
  <h2 class="attr-title">📅 {row.name}</h2>

  <div class="attr-banner" class:stale={ageDays > STALE_DAYS}>
    Data synced {fmtDate(row.synced_at)}
    {#if ageDays > STALE_DAYS}· {ageDays} days old — verify at a visitor center{/if}
  </div>

  <div class="attr-meta-line">
    {#if row.park_code}{row.park_code.toUpperCase()}{/if}
    {#if row.location_text}· {row.location_text}{/if}
    {#if row.is_free}· free{/if}
    {#if row.needs_reservation}· reservation required{/if}
    {#if onShowMap && row.lat != null && row.lon != null}
      <button type="button" class="attr-link" onclick={() => onShowMap!(row!.lat!, row!.lon!, 13)}
        >Show on map</button>
    {/if}
  </div>

  {#if detailText('description')}
    <p class="attr-summary">{detailText('description')}</p>
  {/if}

  <div class="attr-section">
    <h3>Dates ({row.dates.length})</h3>
    <ul class="attr-plain-list">
      {#each row.dates as occ, i (i)}
        <li>
          {occ.date}
          {#if occ.time_start}· {occ.time_start}{#if occ.time_end} – {occ.time_end}{/if}{/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if detailText('feeinfo')}
    <div class="attr-section">
      <h3>Fees</h3>
      <p class="attr-muted">{detailText('feeinfo')}</p>
    </div>
  {/if}

  {#if detailText('regresinfo')}
    <div class="attr-section">
      <h3>Reservations</h3>
      <p class="attr-muted">{detailText('regresinfo')}</p>
    </div>
  {/if}
{/if}
