/**
 * The Places view's browse state — mode, search, filters, selection — as a
 * module singleton so the "google-maps search" session survives tab switches
 * (views remount on route change; losing a half-typed search would hurt).
 * Distinct from the map's layer state (layers.svelte.ts): the map's filter
 * controls pins, this one controls the browser.
 */

import type { PlaceCategory } from '../api'
import { CATEGORY_META } from '../places'

export type BrowseMode = 'places' | 'events'
export type AnchorMode = 'near' | 'everywhere'

class PlacesBrowseStore {
  mode = $state<BrowseMode>('places')
  query = $state('')
  categories = $state<Set<PlaceCategory>>(new Set(CATEGORY_META.map((m) => m.category)))
  anchorMode = $state<AnchorMode>('near')
  /**
   * Browse depth (plan decision 16): off = rank ≤ 3 (common POIs and up);
   * on = every rank, micro furniture included. Search ignores this — `q`
   * always covers all ranks.
   */
  showMinor = $state(false)

  // Selection per mode (kept separately so switching modes keeps both).
  selectedPlace = $state<number | null>(null)
  selectedEvent = $state<number | null>(null)

  /** Mobile only: the detail pane is showing (list otherwise). */
  detailOpen = $state(false)

  /** Toggle a category chip (reassigns the Set so $state reacts). */
  toggleCategory(category: PlaceCategory, on: boolean): void {
    const next = new Set(this.categories)
    if (on) next.add(category)
    else next.delete(category)
    this.categories = next
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
