import { notFoundRoute, routes, type RouteDef } from './routes'

/**
 * Minimal History-API client router. Holds the current path as reactive state
 * and resolves it to a route; no dependency, full control over the (later)
 * keep-the-map-alive pattern. Legacy full-page links bypass it entirely.
 */
class Router {
  path = $state(window.location.pathname)

  constructor() {
    window.addEventListener('popstate', () => {
      this.path = window.location.pathname
    })
  }

  /** The route matching the current path, or the not-found fallback. */
  get current(): RouteDef {
    return routes.find((r) => r.path === this.path) ?? notFoundRoute
  }

  /** Navigate to a client-side route, pushing a history entry. */
  navigate = (to: string): void => {
    if (to === this.path) return
    window.history.pushState({}, '', to)
    this.path = to
  }
}

export const router = new Router()
