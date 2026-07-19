import { onMount } from 'svelte'

import { errMsg } from './errors'

/** Reactive handle returned by {@link poll}: latest payload, last error, manual re-fetch. */
export interface Poller<T> {
  readonly data: T | null
  readonly error: string | null
  refresh(): Promise<void>
}

/**
 * Poll `fetcher` once on mount and every `intervalMs` after, exposing reactive
 * `data`/`error`. The interval is cleared on unmount.
 *
 * A failed fetch stringifies via `errMsg` into `error` and leaves the last good
 * `data` in place (the panel shows stale data with an error note, not a blank).
 *
 * Bridge the getters back to local names with `$derived` to keep call-site
 * templates untouched:
 *
 * ```ts
 * const feed = poll(getGpsdStatus, 30000)
 * let data = $derived(feed.data)
 * let error = $derived(feed.error)
 * ```
 *
 * Args:
 *   fetcher: Async source of the payload; called on the interval.
 *   intervalMs: Refresh cadence in milliseconds.
 *   onData: Optional side-effect run after each successful fetch (e.g. an
 *     "updated at" timestamp).
 */
export function poll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  onData?: (data: T) => void,
): Poller<T> {
  let data = $state<T | null>(null)
  let error = $state<string | null>(null)

  async function refresh(): Promise<void> {
    try {
      data = await fetcher()
      error = null
      onData?.(data)
    } catch (e) {
      error = errMsg(e)
    }
  }

  onMount(() => {
    refresh()
    const timer = window.setInterval(refresh, intervalMs)
    return () => clearInterval(timer)
  })

  return {
    get data() {
      return data
    },
    get error() {
      return error
    },
    refresh,
  }
}
