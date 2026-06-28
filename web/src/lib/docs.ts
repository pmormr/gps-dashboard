// Network-docs rendering helpers. Keeps the markdown engine, mermaid lazy-load,
// and link resolution out of the Svelte view (mirrors globe.ts / skyplot.ts as a
// thin TS module). The view owns UI + navigation; this module is pure-ish DOM.

import MarkdownIt from 'markdown-it'

/** One node in the docs file tree (`GET /api/docs/tree`). */
export interface DocNode {
  name: string
  path: string
  type: 'dir' | 'file'
  children?: DocNode[]
}

/** The `GET /api/docs/tree` document. */
export interface DocsTree {
  available: boolean
  default: string | null
  tree: DocNode[]
}

// Raw HTML is disabled: the vault is self-authored on a trusted LAN, but escaping
// raw HTML removes the only markdown→HTML XSS vector without pulling in a sanitizer.
const md = new MarkdownIt({ html: false, linkify: true, typographer: false })

// Render ```mermaid fences as a placeholder div carrying the (URI-encoded) source;
// renderMermaidBlocks() turns these into SVG after the HTML is in the DOM.
const defaultFence = md.renderer.rules.fence!
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  if (token.info.trim().toLowerCase() === 'mermaid') {
    return `<div class="mermaid-block" data-src="${encodeURIComponent(token.content)}"></div>`
  }
  return defaultFence(tokens, idx, options, env, self)
}

/** Render markdown source to an HTML string (raw HTML escaped). */
export function renderMarkdown(src: string): string {
  return md.render(src)
}

const EXTERNAL = /^([a-z]+:)?\/\//i

/**
 * Resolve an in-doc link href to a vault-relative `.md` path, or null when the
 * link is external, an in-page anchor, or not a markdown doc.
 *
 * @param fromPath - The current doc's vault-relative path (e.g. `devices/pmpi1.md`).
 * @param href - The raw `href` from the rendered markdown.
 */
export function resolveDocHref(fromPath: string, href: string): string | null {
  if (!href || href.startsWith('#') || href.startsWith('mailto:') || EXTERNAL.test(href)) {
    return null
  }
  const [pathPart] = href.split('#')
  if (!pathPart.endsWith('.md')) return null
  const baseDir = fromPath.includes('/') ? fromPath.slice(0, fromPath.lastIndexOf('/') + 1) : ''
  // A throwaway origin lets the URL parser normalise `.`/`..` segments for us.
  const resolved = new URL(pathPart, `https://docs.local/${baseDir}`)
  return decodeURIComponent(resolved.pathname.replace(/^\//, ''))
}

/**
 * Wire up rendered links: internal `.md` links navigate in-app; external links
 * open in a new tab (kiosk-friendly) without leaking the opener.
 *
 * @param container - The rendered-markdown root.
 * @param fromPath - The current doc's vault-relative path (for resolving links).
 * @param navigate - In-app navigation callback, given a vault-relative `.md` path.
 */
export function enhanceLinks(
  container: HTMLElement,
  fromPath: string,
  navigate: (path: string) => void,
): void {
  for (const a of container.querySelectorAll<HTMLAnchorElement>('a[href]')) {
    const href = a.getAttribute('href') ?? ''
    const target = resolveDocHref(fromPath, href)
    if (target) {
      a.addEventListener('click', (e) => {
        e.preventDefault()
        navigate(target)
      })
    } else if (EXTERNAL.test(href) || href.startsWith('mailto:')) {
      a.target = '_blank'
      a.rel = 'noopener noreferrer'
    }
  }
}

let mermaidInit = false

/**
 * Lazy-load mermaid and render every placeholder produced by the fence rule.
 * Heavy (~), so it is dynamic-imported only for docs that actually contain a
 * diagram — it never touches the main bundle.
 *
 * @param container - The rendered-markdown root to scan for `.mermaid-block`s.
 */
export async function renderMermaidBlocks(container: HTMLElement): Promise<void> {
  const blocks = container.querySelectorAll<HTMLElement>('.mermaid-block')
  if (!blocks.length) return

  const mermaid = (await import('mermaid')).default
  if (!mermaidInit) {
    mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' })
    mermaidInit = true
  }

  let i = 0
  for (const block of blocks) {
    const src = decodeURIComponent(block.dataset.src ?? '')
    try {
      const { svg } = await mermaid.render(`mermaid-${Date.now()}-${i++}`, src)
      block.innerHTML = svg
    } catch (e) {
      block.textContent = e instanceof Error ? e.message : String(e)
      block.classList.add('mermaid-error')
    }
  }
}
