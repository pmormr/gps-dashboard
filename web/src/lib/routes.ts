import type { Component } from 'svelte'

import Globe from '../views/Globe.svelte'
import Gpsd from '../views/Gpsd.svelte'
import Home from '../views/Home.svelte'
import NotFound from '../views/NotFound.svelte'
import Ntp from '../views/Ntp.svelte'
import Radio from '../views/Radio.svelte'
import Sky from '../views/Sky.svelte'
import Systems from '../views/Systems.svelte'

/** A client-side route: a path, the view it renders, and the NAV tab it lives under. */
export interface RouteDef {
  path: string
  component: Component
  /** The top-level NAV `to` this route highlights (a sub-route lights its parent). */
  tab: string
}

/**
 * Routes the SPA owns and renders client-side. Paths with a live legacy Jinja
 * page (e.g. /map, /radio) are intentionally absent — they're reached as
 * full-page links during migration (see NAV) and become routes once ported.
 */
export const routes: RouteDef[] = [
  { path: '/', component: Home, tab: '/' },
  { path: '/systems', component: Systems, tab: '/systems' },
  { path: '/sky', component: Sky, tab: '/sky' },
  { path: '/passes', component: Sky, tab: '/sky' },
  { path: '/globe', component: Globe, tab: '/sky' },
  { path: '/ntp', component: Ntp, tab: '/systems' },
  { path: '/gpsd', component: Gpsd, tab: '/systems' },
  { path: '/radio', component: Radio, tab: '/radio' },
]

/** Fallback view for an unmatched client path. */
export const notFoundRoute: RouteDef = { path: '*', component: NotFound, tab: '' }

/**
 * A top-level navigation destination. `to` = a client-side SPA route; `href` =
 * a full-page link to a not-yet-ported legacy page.
 */
export interface NavItem {
  label: string
  icon: string
  to?: string
  href?: string
}

export const NAV: NavItem[] = [
  { label: 'Home', icon: '◉', to: '/' },
  { label: 'Map', icon: '🗺️', href: '/map' },
  { label: 'Systems', icon: '🔋', to: '/systems' },
  { label: 'Sky', icon: '🛰️', to: '/sky' },
  { label: 'Radio', icon: '📻', to: '/radio' },
]
