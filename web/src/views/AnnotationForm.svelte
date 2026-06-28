<script lang="ts">
  import { annotations } from '../lib/stores/annotations.svelte'

  // The create/edit annotation modal (ported from timeline.js). Pure view over
  // `annotations.form`: any opener (Create Range button, drawer ✎) populates the
  // store, this renders it. Save routes POST vs PATCH inside the store by editId.

  let saving = $state(false)

  async function save(): Promise<void> {
    if (saving) return
    saving = true
    try {
      await annotations.save()
    } catch (e) {
      alert(`Failed to save annotation: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      saving = false
    }
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Enter') save()
    if (e.key === 'Escape') annotations.closeForm()
  }
</script>

{#if annotations.form}
  <div
    class="overlay"
    role="presentation"
    onclick={(e) => {
      if (e.target === e.currentTarget) annotations.closeForm()
    }}
  >
    <div class="overlay-card">
      <h3>{annotations.form.title}</h3>
      <label>
        Name
        <!-- svelte-ignore a11y_autofocus -->
        <input
          type="text"
          autocomplete="off"
          placeholder="e.g. Denver to Moab"
          bind:value={annotations.form.name}
          onkeydown={onKeydown}
          autofocus
        />
      </label>
      <label>
        Notes
        <textarea placeholder="Optional notes" bind:value={annotations.form.notes}></textarea>
      </label>
      <p class="muted">{annotations.form.whenLabel}</p>
      <div class="overlay-actions">
        <button class="btn-secondary" onclick={() => annotations.closeForm()}>Cancel</button>
        <button class="btn-primary" disabled={saving} onclick={save}>Save</button>
      </div>
    </div>
  </div>
{/if}
