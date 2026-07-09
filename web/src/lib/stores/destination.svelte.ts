/**
 * The Drive destination — where the chevron points. A module singleton like the
 * other stores, persisted to localStorage so a dash-mounted reload keeps the
 * target.
 *
 * A destination is a **value snapshot, never a live reference**: producers
 * (place detail, a long-pressed pin, later a saved place or trip stop)
 * materialize name + coords at set time. Places imports are full-replace —
 * row ids are not stable across re-syncs — so `source`/`sourceId` are carried
 * as provenance only and must never be dereferenced. The navigation plan
 * attaches a route *alongside* this, not inside it.
 */

export interface Destination {
  /** Display name; null for a raw dropped pin. */
  name: string | null
  lat: number
  lon: number
  /** Provenance, informational only (e.g. 'nps'/'ridb' + that source's id). */
  source?: string
  sourceId?: string
}

const STORAGE_KEY = 'drive-destination'

function load(): Destination | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as Destination
    return typeof d?.lat === 'number' && typeof d?.lon === 'number' ? d : null
  } catch {
    return null
  }
}

class DestinationStore {
  current = $state<Destination | null>(load())

  set(dest: Destination): void {
    this.current = dest
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(dest))
    } catch {
      /* private mode / quota — the in-memory destination still works */
    }
  }

  clear(): void {
    this.current = null
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* ignore */
    }
  }
}

export const destination = new DestinationStore()
