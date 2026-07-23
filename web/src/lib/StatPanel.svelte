<script module lang="ts">
  /** One stat cell inside a {@link StatPanel}'s grid. */
  export interface Stat {
    label: string
    value: string
    /** Small secondary readout under the value (e.g. the °F of a °C temp). */
    alt?: string
    warn?: boolean
  }
</script>

<script lang="ts">
  // Shared glance panel: an uppercase name + a big headline metric over a grid of
  // small stat cells. The one recipe behind Home's status board and Systems' live
  // glance — a headline can carry a tight `unit` suffix (Systems: '%'/'°C') and/or
  // a spaced `alt` word beside it (Home: a decoded state); `dim`/`note`/`warn`
  // cover Home's staleness/fault presentation.
  interface Props {
    name: string
    /** The big metric; units may be baked in (Home) or split into `unit`. */
    headline: string
    /** Tight unit suffix rendered close to the headline (e.g. '%', '°C'). */
    unit?: string
    /** Spaced secondary readout beside the headline (e.g. 'sats used/seen'). */
    alt?: string
    /** Status line under the headline ('no data', a fault, 'stale · 12m'). */
    note?: string
    /** Dim the whole panel — a reading past its freshness window. */
    dim?: boolean
    /** Fault styling on the headline + note. */
    warn?: boolean
    stats?: Stat[]
    /** Min stat-grid column width; smaller packs more cells per row. */
    statMin?: string
  }

  let {
    name,
    headline,
    unit,
    alt,
    note,
    dim = false,
    warn = false,
    stats = [],
    statMin = '90px',
  }: Props = $props()
</script>

<section class="panel" class:dim style="--stat-min: {statMin}">
  <header class="panel-head">
    <span class="panel-name">{name}</span>
    <span class="panel-metric" class:warn-text={warn}>
      {headline}{#if unit}<span class="unit">{unit}</span>{/if}{#if alt}<span class="alt"
          >{alt}</span
        >{/if}
    </span>
  </header>
  {#if note}
    <p class="panel-note" class:warn-text={warn} class:muted={!warn}>{note}</p>
  {/if}
  {#if stats.length}
    <div class="stats">
      {#each stats as st (st.label)}
        <div class="stat">
          <div class="stat-label">{st.label}</div>
          <div class="stat-value" class:warn-text={st.warn}>{st.value}</div>
          {#if st.alt}<div class="stat-alt">{st.alt}</div>{/if}
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
  }
  .panel.dim {
    opacity: 0.5;
  }
  .panel-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }
  .panel-name {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
  }
  .panel-metric {
    font-size: 26px;
    font-weight: 600;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .panel-metric .alt {
    font-size: 13px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 6px;
  }
  .panel-metric .unit {
    font-size: 14px;
    font-weight: 500;
    color: var(--text-dim);
    margin-left: 3px;
  }
  .panel-note {
    margin: 4px 0 0;
    font-size: 12px;
    text-align: right;
  }
  .warn-text {
    color: var(--warn);
  }
  .stats {
    margin-top: 14px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(var(--stat-min), 1fr));
    gap: 12px 16px;
  }
  .stat {
    min-width: 0;
  }
  .stat-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }
  .stat-value {
    margin-top: 2px;
    font-size: 15px;
    font-weight: 500;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .stat-alt {
    font-size: 11px;
    color: var(--text-dim);
  }
</style>
