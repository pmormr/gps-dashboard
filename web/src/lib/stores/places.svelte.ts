/**
 * The Places view's browse state — mode, search, filters, selection — as a
 * module singleton so the "google-maps search" session survives tab switches
 * (views remount on route change; losing a half-typed search would hurt).
 * Distinct from the map's layer state (layers.svelte.ts): the map's kind filter
 * controls pins, this one controls the browser.
 */

import type { PlaceKind } from '../api'
import { KIND_META } from '../places'

export type BrowseMode = 'places' | 'events'
export type AnchorMode = 'near' | 'everywhere'

class PlacesBrowseStore {
  mode = $state<BrowseMode>('places')
  query = $state('')
  kinds = $state<Set<PlaceKind>>(new Set(KIND_META.map((m) => m.kind)))
  anchorMode = $state<AnchorMode>('near')

  // Selection per mode (kept separately so switching modes keeps both).
  selectedPlace = $state<number | null>(null)
  selectedEvent = $state<number | null>(null)

  /** Mobile only: the detail pane is showing (list otherwise). */
  detailOpen = $state(false)

  /** Toggle a kind chip (reassigns the Set so $state reacts). */
  toggleKind(kind: PlaceKind, on: boolean): void {
    const next = new Set(this.kinds)
    if (on) next.add(kind)
    else next.delete(kind)
    this.kinds = next
  }

  /** The current mode's selected row id (null = nothing selected). */
  get selected(): number | null {
    return this.mode === 'places' ? this.selectedPlace : this.selectedEvent
  }

  select(id: number): void {
    if (this.mode === 'places') this.selectedPlace = id
    else this.selectedEvent = id
    this.detailOpen = true
  }
}

export const browse = new PlacesBrowseStore()
