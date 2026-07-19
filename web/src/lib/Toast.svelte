<script lang="ts">
  import type { ToastController } from './useToast.svelte'

  let { c }: { c: ToastController } = $props()
</script>

<div class="toast" class:show={c.show} class:err={c.err}>{c.msg}</div>

<style>
  .toast {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: calc(var(--nav-h) + env(safe-area-inset-bottom) + 16px);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    opacity: 0;
    transition: opacity 0.2s;
    pointer-events: none;
    max-width: 90vw;
    z-index: 200;
  }
  .toast.show {
    opacity: 1;
  }
  .toast.err {
    border-color: var(--err);
    color: #fecaca;
  }

  @media (min-width: 768px) {
    .toast {
      bottom: 20px;
    }
  }
</style>
