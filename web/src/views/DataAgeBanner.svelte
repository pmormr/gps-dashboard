<script lang="ts">
  import type { Snippet } from 'svelte'

  import { fmtDate } from '../lib/geo'

  // The always-on "Data synced <date>" banner shared by the place and event
  // detail panes. Past `staleDays` it escalates to a warning and appends the
  // caller's source-specific call to action. Styled by the global .attr-banner
  // rule in places.css (loaded by Map/Places).
  let {
    syncedAt,
    staleDays,
    action,
  }: { syncedAt: string; staleDays: number; action?: Snippet } = $props()

  const ageDays = $derived(Math.floor((Date.now() - new Date(syncedAt).getTime()) / 86_400_000))
  const isStale = $derived(ageDays > staleDays)
</script>

<div class="attr-banner" class:stale={isStale}>
  Data synced {fmtDate(syncedAt)}
  {#if isStale}· {ageDays} days old — {@render action?.()}{/if}
</div>
