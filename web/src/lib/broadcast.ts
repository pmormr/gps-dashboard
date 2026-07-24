/** Broadcast tab helpers: feed-config types, clipboard, and slot grouping.
 *
 * The types mirror `broadcast/feeds.py`'s `render_feeds` payload (the Flask route
 * `/api/broadcast/feeds`). The view is a grab-and-go config reference — every
 * feed's copy-ready send/OBS strings, grouped by slot — so the helpers here are
 * the pure copy + grouping logic; the view owns the fetch + per-button feedback.
 */

/** Publisher-side send config (interpolated); only transport-relevant fields set. */
export interface SendConfig {
  host: string
  port: number
  streamid: string | null
  passphrase: string | null
  latency_ms: number | null
  encryption: string | null
  /** Derived one-field send URL (SRT/RTMP), for apps that take a single field. */
  single_url: string | null
}

/** One rendered feed — a MediaMTX path on one hub, config + status expectations. */
export interface Feed {
  path: string
  label: string
  hub: 'van' | 'cloud'
  slot_group: string
  transport: string
  role: string
  standby: boolean
  expected_tracks: string[]
  obs_read: string | null
  browser_url: string | null
  notes: string[]
  send: SendConfig | null
  /** Env keys this feed needs but the server env lacked (unresolved `${…}`). */
  missing_secrets: string[]
}

/** The `/api/broadcast/feeds` payload. */
export interface BroadcastFeeds {
  feeds: Feed[]
  /** Union of unresolved env keys across all feeds (flag an unconfigured env file). */
  missing_secrets: string[]
}

/** Slot-group display order + labels (registry order, grouped for the UI). */
export const SLOT_GROUPS: { key: string; label: string }[] = [
  { key: 'cameras', label: 'PtP Cameras' },
  { key: 'phones', label: 'Phones' },
  { key: 'drones', label: 'Drones' },
  { key: 'radio', label: 'Radio' },
  { key: 'security', label: 'Security B-roll' },
]

/** Group feeds by `slot_group` in {@link SLOT_GROUPS} order (empty groups dropped). */
export function groupBySlot(feeds: Feed[]): { key: string; label: string; feeds: Feed[] }[] {
  return SLOT_GROUPS.map((g) => ({
    ...g,
    feeds: feeds.filter((f) => f.slot_group === g.key),
  })).filter((g) => g.feeds.length > 0)
}

/**
 * Copy text to the clipboard, working on the plain-HTTP LAN.
 *
 * The dashboard is served over `http://<ip>` (not HTTPS/localhost), which is an
 * *insecure context* — `navigator.clipboard` is unavailable there. So try the
 * async Clipboard API when it's actually usable, then fall back to the legacy
 * `execCommand('copy')` over a hidden textarea (the only path that works on the
 * van LAN). Returns whether the copy succeeded so the caller can show feedback.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // fall through to the legacy path
    }
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}
