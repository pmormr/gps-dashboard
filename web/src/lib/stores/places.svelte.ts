/**
 * The Places view's browse state — mode, search, filters, selection — as a
 * module singleton so the "google-maps search" session survives tab switches
 * (views remount on route change; losing a half-typed search would hurt).
 * Distinct from the map's layer state (layers.svelte.ts): the map's filter
 * controls pins, this one controls the browser.
 */

import { CATEGORY_GROUPS } from '../places'

export type BrowseMode = 'places' | 'events'
export type AnchorMode = 'near' | 'everywhere'

class PlacesBrowseStore {
  mode = $state<BrowseMode>('places')
  query = $state('')
  /**
   * Coarse filter: category *groups* (CATEGORY_GROUPS), not the 19 raw
   * categories — fine-grained refinement is the kind facets below. The
   * Services group (infrastructure/civic noise) defaults off in browse;
   * search ignores groups entirely (`q` covers every category).
   */
  groups = $state<Set<string>>(
    new Set(CATEGORY_GROUPS.filter((g) => g.defaultOn).map((g) => g.key)),
  )
  /**
   * Fine filter: facet-selected `source_kind` values ('amenity=cafe',
   * 'campground'). Applies to browse and search alike; cleared on a new
   * query (a stale type refinement against fresh search text is a trap).
   */
  kinds = $state<Set<string>>(new Set())
  anchorMode = $state<AnchorMode>('near')
  /**
   * Browse depth: off = rank ≤ 3 (common POIs and up); on = every rank,
   * micro furniture included. Search ignores this — `q` always covers all
   * ranks.
   */
  showMinor = $state(false)

  // Selection per mode (kept separately so switching modes keeps both).
  selectedPlace = $state<number | null>(null)
  selectedEvent = $state<number | null>(null)

  /** Mobile only: the detail pane is showing (list otherwise). */
  detailOpen = $state(false)

  /** Toggle a group chip (reassigns the Set so $state reacts). */
  toggleGroup(key: string, on: boolean): void {
    const next = new Set(this.groups)
    if (on) next.add(key)
    else next.delete(key)
    this.groups = next
  }

  /** Toggle a facet kind chip (reassigns the Set so $state reacts). */
  toggleKind(kind: string, on: boolean): void {
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
