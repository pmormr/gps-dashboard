<script lang="ts">
  import { getObdEconomy, type Annotation } from '../lib/api'
  import { fmtDate, fmtTime } from '../lib/geo'
  import { annotations } from '../lib/stores/annotations.svelte'

  // The annotations drawer (side panel desktop / bottom sheet mobile).
  // Lists every annotation; click jumps the global window to it,
  // ✎ edits, × deletes. Per-range fuel economy is lazy-filled (derived OBD fuel ÷
  // GPS distance) and skipped where there's no OBD coverage.
  let { open = $bindable(false) }: { open?: boolean } = $props()

  // Lazy per-range economy labels, keyed by annotation id.
  let econ = $state<Record<number, string>>({})

  $effect(() => {
    for (const a of annotations.list) {
      if (!a.end_time) continue
      getObdEconomy(a.start_time, a.end_time)
        .then((e) => {
          if (!e || !e.n_obd_rows || e.mpg == null) return
          econ[a.id] =
            `⛽ ${e.mpg.toFixed(1)} mpg · ${e.distance_mi.toFixed(1)} mi · ${e.fuel_gallons.toFixed(2)} gal`
        })
        .catch(() => {
          /* no OBD coverage for this window — leave blank */
        })
    }
  })

  function meta(a: Annotation): string {
    if (!a.end_time) return `${fmtDate(a.start_time)} · ${fmtTime(a.start_time)}`
    const bounds = `${fmtDate(a.start_time)} · ${fmtTime(a.start_time)} → ${fmtTime(a.end_time)}`
    return a.point_count != null ? `${bounds} · ${a.point_count.toLocaleString()} pts` : bounds
  }

  async function confirmDelete(a: Annotation): Promise<void> {
    if (!confirm(`Delete "${a.name}"?`)) return
    try {
      await annotations.remove(a.id)
    } catch (e) {
      alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }
</script>

<aside class="ann-drawer" class:hidden={!open}>
  <div class="ann-drawer-header">
    <h2>Annotations</h2>
    <button class="btn-icon" title="Close drawer" onclick={() => (open = false)}>×</button>
  </div>
  <div class="ann-list">
    {#if annotations.loadError}
      <p class="error">Failed to load: {annotations.loadError}</p>
    {:else if !annotations.list.length}
      <p class="empty-state">
        No annotations yet. Mark a range on the slider and Create Range, or Bookmark Here for a
        single moment.
      </p>
    {:else}
      {#each annotations.list as a (a.id)}
        <div
          class="annotation-item"
          role="button"
          tabindex="0"
          onclick={() => annotations.jumpTo(a)}
          onkeydown={(e) => {
            if (e.key === 'Enter') annotations.jumpTo(a)
          }}
        >
          <span class="annotation-type">{a.end_time ? '⇄' : '📍'}</span>
          <div class="annotation-item-main">
            <span class="annotation-name">{a.name}</span>
            <span class="annotation-meta">{meta(a)}</span>
            {#if a.notes}<span class="annotation-notes">{a.notes}</span>{/if}
            {#if a.end_time && econ[a.id]}<span class="annotation-econ">{econ[a.id]}</span>{/if}
          </div>
          <div class="annotation-actions">
            <button
              class="annotation-edit-btn"
              title="Edit"
              onclick={(e) => {
                e.stopPropagation()
                annotations.openEdit(a)
              }}>✎</button
            >
            <button
              class="annotation-delete-btn"
              title="Delete"
              onclick={(e) => {
                e.stopPropagation()
                confirmDelete(a)
              }}>×</button
            >
          </div>
        </div>
      {/each}
    {/if}
  </div>
</aside>
