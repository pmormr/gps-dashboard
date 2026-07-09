import type { Component } from 'svelte'

import Places from '../views/Places.svelte'
import Docs from '../views/Docs.svelte'
import Drive from '../views/Drive.svelte'
import Globe from '../views/Globe.svelte'
import Gpsd from '../views/Gpsd.svelte'
import Home from '../views/Home.svelte'
import Map from '../views/Map.svelte'
import NotFound from '../views/NotFound.svelte'
import Ntp from '../views/Ntp.svelte'
import Radio from '../views/Radio.svelte'
import Skyplot from '../views/Skyplot.svelte'
import Sky from '../views/Sky.svelte'
import Systems from '../views/Systems.svelte'
import Trends from '../views/Trends.svelte'

/** A client-side route: a path, the view it renders, and the NAV tab it lives under. */
export interface RouteDef {
  path: string
  component: Component
  /** The top-level NAV `to` this route highlights (a sub-route lights its parent). */
  tab: string
  /** Match `path` and any `path/...` sub-path (e.g. `/docs/devices/pmpi1.md`). */
  prefix?: boolean
}

/** Routes the SPA owns and renders client-side. Every top-level destination + drill-in. */
export const routes: RouteDef[] = [
  { path: '/', component: Home, tab: '/' },
  { path: '/map', component: Map, tab: '/map' },
  { path: '/drive', component: Drive, tab: '/drive' },
  { path: '/places', component: Places, tab: '/places' },
  { path: '/systems', component: Systems, tab: '/systems' },
  { path: '/docs', component: Docs, tab: '/docs', prefix: true },
  { path: '/sky', component: Sky, tab: '/sky' },
  { path: '/passes', component: Sky, tab: '/sky' },
  { path: '/globe', component: Globe, tab: '/sky' },
  { path: '/skyplot', component: Skyplot, tab: '/sky' },
  { path: '/ntp', component: Ntp, tab: '/systems' },
  { path: '/gpsd', component: Gpsd, tab: '/systems' },
  { path: '/trends', component: Trends, tab: '/systems' },
  { path: '/radio', component: Radio, tab: '/radio' },
]

/** Fallback view for an unmatched client path. */
export const notFoundRoute: RouteDef = { path: '*', component: NotFound, tab: '' }

/** A top-level navigation destination — a client-side SPA route. */
export interface NavItem {
  label: string
  icon: string
  to: string
}

export const NAV: NavItem[] = [
  { label: 'Home', icon: '◉', to: '/' },
  { label: 'Map', icon: '🗺️', to: '/map' },
  { label: 'Drive', icon: '🧭', to: '/drive' },
  { label: 'Places', icon: '🏞️', to: '/places' },
  { label: 'Systems', icon: '🔋', to: '/systems' },
  { label: 'Docs', icon: '📓', to: '/docs' },
  { label: 'Sky', icon: '🛰️', to: '/sky' },
  { label: 'Radio', icon: '📻', to: '/radio' },
]
