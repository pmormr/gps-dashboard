<script lang="ts">
  import Shell from './lib/Shell.svelte'
  import { router } from './lib/router.svelte'

  const Current = $derived(router.current.component)
</script>

<Shell>
  <Current />
</Shell>
