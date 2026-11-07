<script lang="ts">
  import Shell from './lib/Shell.svelte'
  import { router } from './lib/router.svelte'
  import { MAP_CHROME } from './lib/routes'
  import MapChrome from './views/MapChrome.svelte'

  const Current = $derived(router.current.component)
  // Shell-owned chrome over the shared map engine, beside the routed map view
  // (routes.ts MAP_CHROME). Keyed by path so it remounts per map route.
  const chrome = $derived(MAP_CHROME[router.current.path])
</script>

<Shell>
  <Current />
  {#if chrome}
    {#key router.current.path}
      <MapChrome {chrome} />
    {/key}
  {/if}
</Shell>
