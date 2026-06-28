<script lang="ts">
  import { onMount } from 'svelte'

  import { getMarks, markTimestamp, type Marks } from '../lib/api'
  import { selection } from '../lib/stores/selection.svelte'

  // The Marks floating panel (ported from the marks half of timeline.js). Marks
  // are persisted live range-construction timestamps (two rows: start/end); once
  // both are set, "Use Marks" reframes the global window to that range. Self-
  // contained — marks have no other consumer, so state lives here, not in a store.
  let collapsed = $state(true)
  let marks = $state<Marks>({})

  let hasBoth = $derived(!!marks.start && !!marks.end)
  let status = $derived(
    marks.start || marks.end ? `S: ${fmt(marks.start)}  E: ${fmt(marks.end)}` : '',
  )

  function fmt(iso?: string): string {
    if (!iso) return '—'
    const d = new Date(iso)
    return (
      d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
      ' ' +
      d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    )
  }

  async function mark(marker: 'start' | 'end'): Promise<void> {
    try {
      marks = await markTimestamp(marker)
    } catch (e) {
      alert(`Mark failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  function useMarks(): void {
    if (marks.start && marks.end) selection.setRange(new Date(marks.start), new Date(marks.end))
  }

  onMount(async () => {
    try {
      marks = await getMarks()
    } catch {
      marks = {}
    }
  })
</script>

<div class="floating-panel" class:collapsed>
  <button class="floating-panel-hdr" type="button" onclick={() => (collapsed = !collapsed)}>
    🚩 Marks
  </button>
  <div class="floating-panel-body">
    <div class="tl-mark-row">
      <button class="btn-secondary" onclick={() => mark('start')}>Mark Start</button>
      <button class="btn-secondary" onclick={() => mark('end')}>Mark End</button>
      {#if hasBoth}<button class="btn-secondary" onclick={useMarks}>Use Marks</button>{/if}
    </div>
    {#if status}<p class="tl-mark-status muted">{status}</p>{/if}
  </div>
</div>
