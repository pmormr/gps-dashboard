<script lang="ts">
  import { onMount, tick } from 'svelte'

  import { getDocFile, getDocsTree, putDocFile } from '../lib/api'
  import {
    type DocNode,
    type DocsTree,
    enhanceLinks,
    renderMarkdown,
    renderMermaidBlocks,
  } from '../lib/docs'
  import type { DocEditor } from '../lib/docsEditor'
  import { router } from '../lib/router.svelte'

  let treeData = $state<DocsTree | null>(null)
  let treeError = $state<string | null>(null)
  let docError = $state<string | null>(null)
  let loading = $state(false)
  let contentEl = $state<HTMLDivElement | undefined>()
  let navOpen = $state(false)

  // Edit mode: a CodeMirror editor (lazy module) over the loaded doc's raw source.
  let editorEl = $state<HTMLDivElement | undefined>()
  let editing = $state(false)
  let previewing = $state(false)
  let saving = $state(false)
  let saveError = $state<string | null>(null)
  let saveNotice = $state<string | null>(null)
  let editor: DocEditor | null = null
  let docSource = ''
  let docEtag = ''

  // The active doc: an explicit /docs/<path>, else the tree's default once loaded.
  const docPath = $derived(
    router.path.startsWith('/docs/')
      ? decodeURIComponent(router.path.slice(6))
      : (treeData?.default ?? null),
  )

  onMount(async () => {
    try {
      treeData = await getDocsTree()
    } catch (e) {
      treeError = e instanceof Error ? e.message : String(e)
    }
  })

  let loadToken = 0

  async function loadDoc(path: string, el: HTMLElement): Promise<void> {
    const token = ++loadToken
    loading = true
    docError = null
    try {
      const doc = await getDocFile(path)
      if (token !== loadToken) return
      docSource = doc.content
      docEtag = doc.etag
      el.innerHTML = renderMarkdown(doc.content)
      enhanceLinks(el, path, open)
      await renderMermaidBlocks(el)
    } catch (e) {
      if (token !== loadToken) return
      docError = e instanceof Error ? e.message : String(e)
      el.innerHTML = ''
    } finally {
      if (token === loadToken) loading = false
    }
  }

  $effect(() => {
    const path = docPath
    const el = contentEl
    if (!el) return
    closeEditor()
    saveNotice = null
    if (!path) {
      el.innerHTML = ''
      return
    }
    void loadDoc(path, el)
  })

  // Losing a dirty draft to a tab close is the one path git can't recover.
  $effect(() => {
    if (!editing) return
    const guard = (e: BeforeUnloadEvent) => {
      if (editor?.isDirty()) e.preventDefault()
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  })

  function confirmDiscard(): boolean {
    return !editor?.isDirty() || confirm('Discard unsaved changes?')
  }

  function closeEditor(): void {
    editor?.destroy()
    editor = null
    editing = false
    previewing = false
    saveError = null
  }

  async function startEdit(): Promise<void> {
    if (!docPath || editing) return
    saveNotice = null
    editing = true // mounts the editor container
    const { createEditor } = await import('../lib/docsEditor')
    await tick()
    if (!editing || !editorEl) return
    editor = createEditor(editorEl, docSource)
    editor.focus()
  }

  function togglePreview(): void {
    if (!editor || !contentEl) return
    previewing = !previewing
    if (previewing) {
      contentEl.innerHTML = renderMarkdown(editor.getValue())
      void renderMermaidBlocks(contentEl)
    }
  }

  function cancelEdit(): void {
    if (!confirmDiscard()) return
    closeEditor()
    if (contentEl) contentEl.innerHTML = renderMarkdown(docSource)
    if (contentEl && docPath) enhanceLinks(contentEl, docPath, open)
    if (contentEl) void renderMermaidBlocks(contentEl)
  }

  async function saveEdit(): Promise<void> {
    if (!editor || !docPath || saving) return
    const draft = editor.getValue()
    saving = true
    saveError = null
    try {
      const result = await putDocFile(docPath, draft, docEtag)
      docSource = draft
      docEtag = result.etag
      closeEditor()
      saveNotice = result.committed ? 'Saved · committed' : 'Saved (no repo — not committed)'
      if (contentEl) {
        contentEl.innerHTML = renderMarkdown(draft)
        enhanceLinks(contentEl, docPath, open)
        void renderMermaidBlocks(contentEl)
      }
    } catch (e) {
      saveError =
        e instanceof Error && e.message === 'conflict'
          ? 'This file changed since you opened it (a push or another editor). Copy your draft, reload the doc, and re-apply.'
          : `Save failed — ${e instanceof Error ? e.message : String(e)}`
    } finally {
      saving = false
    }
  }

  function open(path: string): void {
    if (editing && !confirmDiscard()) return
    router.navigate(`/docs/${path}`)
    navOpen = false
  }

  const label = (name: string): string => name.replace(/\.md$/, '')
</script>

<header class="page-head">
  <h1>Docs</h1>
  <p class="muted">
    Network documentation{#if docPath} — <span class="mono">{docPath}</span>{/if}
  </p>
</header>

{#if treeError}
  <div class="banner err">Couldn't load the docs index — {treeError}</div>
{:else if treeData && !treeData.available}
  <div class="banner">
    Network docs aren't synced to this device yet. Push the <code>paul-network-docs</code> repo to
    the Pi (<code>git push pi main</code>) to populate this tab.
  </div>
{:else if treeData}
  <button class="files-toggle" onclick={() => (navOpen = !navOpen)}>📁 Files</button>

  <div class="docs-layout">
    <aside class="tree" class:open={navOpen}>
      {#snippet nodes(items: DocNode[], depth: number)}
        {#each items as node (node.path)}
          {#if node.type === 'dir'}
            <div class="tree-dir" style="padding-left: {depth * 12 + 10}px">{node.name}</div>
            {@render nodes(node.children ?? [], depth + 1)}
          {:else}
            <button
              class="tree-file"
              class:active={node.path === docPath}
              style="padding-left: {depth * 12 + 10}px"
              onclick={() => open(node.path)}>{label(node.name)}</button>
          {/if}
        {/each}
      {/snippet}
      {@render nodes(treeData.tree, 0)}
    </aside>

    <main class="doc">
      {#if docPath && !loading && !docError}
        <div class="doc-toolbar">
          {#if editing}
            <button class="tool" onclick={togglePreview}>
              {previewing ? '✎ Editor' : '👁 Preview'}
            </button>
            <button class="tool" onclick={cancelEdit} disabled={saving}>Cancel</button>
            <button class="tool primary" onclick={saveEdit} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          {:else}
            <button class="tool" onclick={startEdit}>✎ Edit</button>
            {#if saveNotice}<span class="save-notice">{saveNotice}</span>{/if}
          {/if}
        </div>
      {/if}
      {#if loading}<div class="muted loading">Loading…</div>{/if}
      {#if docError}<div class="banner err">{docError}</div>{/if}
      {#if saveError}<div class="banner err">{saveError}</div>{/if}
      {#if editing}
        <div class="editor" class:hidden={previewing} bind:this={editorEl}></div>
      {/if}
      <div class="doc-body" class:hidden={editing && !previewing} bind:this={contentEl}></div>
    </main>
  </div>
{:else}
  <p class="muted">Loading…</p>
{/if}

<style>
  .banner {
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 16px;
    background: var(--surface);
    border: 1px solid var(--border);
  }
  .banner.err {
    background: rgba(239, 83, 80, 0.12);
    color: var(--err);
    border-color: rgba(239, 83, 80, 0.4);
  }
  .banner code {
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 4px;
  }

  .mono {
    font-family: ui-monospace, monospace;
    font-size: 13px;
  }

  .files-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 12px;
    padding: 8px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }

  .docs-layout {
    display: flex;
    gap: 20px;
    align-items: flex-start;
  }

  .tree {
    display: none;
    flex-direction: column;
    width: 240px;
    flex-shrink: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 10px 8px;
    overflow: hidden;
  }
  .tree.open {
    display: flex;
  }

  .tree-dir {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    padding: 10px 0 4px;
  }

  .tree-file {
    display: block;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    border-radius: 6px;
    color: var(--text);
    font: inherit;
    font-size: 14px;
    padding-top: 6px;
    padding-bottom: 6px;
    padding-right: 8px;
    cursor: pointer;
  }
  .tree-file:hover {
    background: var(--surface-2);
  }
  .tree-file.active {
    background: var(--accent-dim);
    color: var(--accent);
  }

  .doc {
    flex: 1;
    min-width: 0;
  }
  .loading {
    margin-bottom: 12px;
  }

  .doc-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
  }
  .tool {
    padding: 6px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .tool:hover:not(:disabled) {
    background: var(--surface-2);
  }
  .tool:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .tool.primary {
    background: var(--accent-dim);
    color: var(--accent);
    border-color: var(--accent);
  }
  .save-notice {
    font-size: 13px;
    color: var(--text-dim);
  }

  .editor {
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }
  .editor :global(.cm-editor) {
    font-size: 14px;
    max-height: 75vh;
  }
  .editor :global(.cm-scroller) {
    overflow: auto;
  }
  .hidden {
    display: none;
  }

  /* Rendered-markdown styles. The body is filled imperatively (innerHTML), so the
     scoping class lands on .doc-body only — descendants need :global(). */
  .doc-body {
    line-height: 1.6;
    word-wrap: break-word;
  }
  .doc-body :global(h1) {
    font-size: 26px;
    margin: 0 0 16px;
  }
  .doc-body :global(h2) {
    font-size: 20px;
    margin: 28px 0 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .doc-body :global(h3) {
    font-size: 16px;
    margin: 22px 0 10px;
  }
  .doc-body :global(h4) {
    font-size: 14px;
    margin: 18px 0 8px;
  }
  .doc-body :global(p),
  .doc-body :global(ul),
  .doc-body :global(ol) {
    margin: 0 0 14px;
  }
  .doc-body :global(li) {
    margin: 4px 0;
  }
  .doc-body :global(a) {
    color: var(--accent);
  }
  .doc-body :global(code) {
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.9em;
  }
  .doc-body :global(pre) {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
    overflow-x: auto;
  }
  .doc-body :global(pre code) {
    background: none;
    padding: 0;
  }
  .doc-body :global(blockquote) {
    margin: 0 0 14px;
    padding: 2px 14px;
    border-left: 3px solid var(--border);
    color: var(--text-dim);
  }
  .doc-body :global(table) {
    display: block;
    width: max-content;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    margin: 0 0 16px;
    font-size: 14px;
  }
  .doc-body :global(th),
  .doc-body :global(td) {
    border: 1px solid var(--border);
    padding: 7px 11px;
    text-align: left;
  }
  .doc-body :global(th) {
    background: var(--surface);
    font-weight: 600;
  }
  .doc-body :global(hr) {
    border: none;
    border-top: 1px solid var(--border);
    margin: 24px 0;
  }
  .doc-body :global(img) {
    max-width: 100%;
  }
  .doc-body :global(.mermaid-block) {
    display: flex;
    justify-content: center;
    margin: 0 0 16px;
  }
  .doc-body :global(.mermaid-block svg) {
    max-width: 100%;
    height: auto;
  }
  .doc-body :global(.mermaid-error) {
    display: block;
    color: var(--err);
    font-family: ui-monospace, monospace;
    font-size: 13px;
    white-space: pre-wrap;
  }

  @media (min-width: 768px) {
    .files-toggle {
      display: none;
    }
    .tree {
      display: flex;
      position: sticky;
      top: 24px;
    }
  }
</style>
