import type { Component } from 'svelte'

import Home from '../views/Home.svelte'
import NotFound from '../views/NotFound.svelte'
import Ntp from '../views/Ntp.svelte'
import Sky from '../views/Sky.svelte'
import Systems from '../views/Systems.svelte'

/** A client-side route: a path owned by the SPA and the view it renders. */
export interface RouteDef {
  path: string
  component: Component
}

/**
 * Routes the SPA owns and renders client-side. Paths with a live legacy Jinja
 * page (e.g. /map, /radio) are intentionally absent — they're reached as
 * full-page links during migration (see NAV) and become routes once ported.
 */
export const routes: RouteDef[] = [
  { path: '/', component: Home },
  { path: '/systems', component: Systems },
  { path: '/sky', component: Sky },
  { path: '/ntp', component: Ntp },
]

/** Fallback view for an unmatched client path. */
export const notFoundRoute: RouteDef = { path: '*', component: NotFound }

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
  { label: 'Radio', icon: '📻', href: '/radio' },
]
