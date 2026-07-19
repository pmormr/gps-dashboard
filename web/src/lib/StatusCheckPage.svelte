<script lang="ts">
  import type { Snippet } from 'svelte'

  import type { StatusCheck } from './api'

  /**
   * Shared chrome for the check-driven status pages (gpsd, NTP): page header,
   * pass/fail banner, the Checks list, and the loading state. Views pass their
   * own detail panels as `children`.
   *
   * The detail panels are authored in the parent view and rendered here as a
   * snippet, so the parent's *scoped* styles can't reach the row/panel classes.
   * Everything is wrapped in `.status-check` and the shared row/panel CSS is
   * declared `:global(.status-check …)` — scoped to descendants of the wrapper,
   * which reaches the snippet content without colliding with the unrelated
   * `.panel` definitions in Home/Systems/Data (none sit under `.status-check`).
   */
  let {
    title,
    subtitle,
    error,
    status,
    children,
  }: {
    title: string
    subtitle: string
    error: string | null
    status: { overall_ok: boolean; checks: StatusCheck[] } | null
    children?: Snippet
  } = $props()
</script>

<div class="status-check">
  <header class="page-head">
    <h1>{title}</h1>
    <p class="muted">
      {subtitle}{#if error} — <span class="err-text">{error}</span>{/if}
    </p>
  </header>

  {#if status}
    <div class="status-banner {status.overall_ok ? 'ok' : 'err'}">
      {status.overall_ok ? '✓ All checks passing' : '✗ One or more checks failing'}
    </div>

    <section class="panel">
      <div class="panel-title eyebrow">Checks</div>
      {#each status.checks as c (c.name)}
        <div class="row">
          <span class="dot {c.ok ? 'ok' : 'err'}"></span>
          <span class="grow">{c.name}</span>
          <span class="tag {c.ok ? 'ok' : 'err'}">{c.ok ? 'PASS' : 'FAIL'}</span>
        </div>
      {/each}
    </section>

    {@render children?.()}
  {:else if !error}
    <p class="muted">Loading…</p>
  {/if}
</div>

<style>
  /* Shared with the parent's snippet content — scoped to the wrapper, not global. */
  :global(.status-check .err-text) {
    color: var(--err);
  }

  :global(.status-check .panel) {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    overflow: hidden;
  }
  :global(.status-check .panel-title) {
    padding: 12px 14px 4px;
  }

  :global(.status-check .row),
  :global(.status-check .kv) {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    border-top: 1px solid var(--border);
  }
  :global(.status-check .panel-title + .row),
  :global(.status-check .panel-title + .kv) {
    border-top: none;
  }

  :global(.status-check .grow) {
    flex: 1;
  }
  :global(.status-check .kv .k) {
    color: var(--text-dim);
  }
  :global(.status-check .kv .v) {
    margin-left: auto;
    text-align: right;
    font-weight: 500;
    word-break: break-all;
  }
  :global(.status-check .v.ok) {
    color: var(--ok);
  }
  :global(.status-check .v.err) {
    color: var(--err);
  }
  :global(.status-check .v.warn) {
    color: var(--warn);
  }

  :global(.status-check .dot) {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  :global(.status-check .dot.ok) {
    background: var(--ok);
  }
  :global(.status-check .dot.err) {
    background: var(--err);
  }

  :global(.status-check .tag) {
    font-size: 12px;
    font-weight: 600;
  }
  :global(.status-check .tag.ok) {
    color: var(--ok);
  }
  :global(.status-check .tag.err) {
    color: var(--err);
  }
</style>
