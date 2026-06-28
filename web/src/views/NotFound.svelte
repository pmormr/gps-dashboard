<script lang="ts">
  import { router } from '../lib/router.svelte'
</script>

<header class="page-head">
  <h1>Not found</h1>
  <p class="muted">No view for <code>{router.path}</code>.</p>
</header>

<button class="link-row" style="cursor: pointer" onclick={() => router.navigate('/')}>
  <span class="link-label">Go to Home</span>
</button>
