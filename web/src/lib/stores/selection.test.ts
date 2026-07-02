/**
 * Selection-store unit tests — the zoom history (zoomTo/back/resetZoom) and the
 * shift/widen navigation math (time-dock plan Phase 1). Runs in node: the store
 * skips its live poll timer without a DOM, and `vi.setSystemTime` pins `now` for
 * the live-window paths.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SelectionStore } from './selection.svelte'

const HOUR = 3600_000
const DAY = 24 * HOUR
const YEAR = 365 * DAY
const NOW = new Date('2026-07-02T12:00:00Z')

let store: SelectionStore

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  store = new SelectionStore()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('zoom history', () => {
  it('zoomTo sets an explicit range and pushes the prior window', () => {
    expect(store.canGoBack).toBe(false)
    const from = new Date('2026-07-01T00:00:00Z')
    const to = new Date('2026-07-01T06:00:00Z')
    store.zoomTo(from, to)
    expect(store.range).toMatchObject({ from, to, mode: 'range', live: false })
    expect(store.canGoBack).toBe(true)
  })

  it('back() restores the pushed picker state, resuming Live out of a zoom', () => {
    expect(store.live).toBe(true) // default: Live · Last 24h
    store.zoomTo(new Date('2026-07-01T00:00:00Z'), new Date('2026-07-01T06:00:00Z'))
    expect(store.live).toBe(false)
    store.back()
    expect(store.live).toBe(true)
    expect(store.mode).toBe('last')
    expect(store.windowMs).toBe(DAY)
    expect(store.canGoBack).toBe(false)
  })

  it('back() pops one level at a time through nested zooms', () => {
    store.zoomTo(new Date('2026-07-01T00:00:00Z'), new Date('2026-07-01T12:00:00Z'))
    store.zoomTo(new Date('2026-07-01T02:00:00Z'), new Date('2026-07-01T04:00:00Z'))
    store.back()
    expect(store.range.from).toEqual(new Date('2026-07-01T00:00:00Z'))
    expect(store.range.to).toEqual(new Date('2026-07-01T12:00:00Z'))
    expect(store.canGoBack).toBe(true)
  })

  it('back() on an empty stack is a no-op', () => {
    const before = store.range
    store.back()
    expect(store.range).toEqual(before)
  })

  it('goLive() resets to Live · Last 24h and clears the history', () => {
    store.zoomTo(new Date('2026-07-01T00:00:00Z'), new Date('2026-07-01T12:00:00Z'))
    store.shift(-1)
    store.goLive()
    expect(store.live).toBe(true)
    expect(store.mode).toBe('last')
    expect(store.windowMs).toBe(DAY)
    expect(store.canGoBack).toBe(false)
    expect(store.range.to).toEqual(NOW)
  })

  it('resetZoom() restores the window before the first zoom and clears the stack', () => {
    store.zoomTo(new Date('2026-07-01T00:00:00Z'), new Date('2026-07-01T12:00:00Z'))
    store.zoomTo(new Date('2026-07-01T02:00:00Z'), new Date('2026-07-01T04:00:00Z'))
    store.resetZoom()
    expect(store.live).toBe(true)
    expect(store.mode).toBe('last')
    expect(store.canGoBack).toBe(false)
  })
})

describe('shift', () => {
  it('moves an explicit range by one window-width', () => {
    store.setRange(new Date('2026-07-01T00:00:00Z'), new Date('2026-07-01T06:00:00Z'))
    store.shift(1)
    expect(store.range.from).toEqual(new Date('2026-07-01T06:00:00Z'))
    expect(store.range.to).toEqual(new Date('2026-07-01T12:00:00Z'))
    store.shift(-1)
    store.shift(-1)
    expect(store.range.from).toEqual(new Date('2026-06-30T18:00:00Z'))
    expect(store.range.to).toEqual(new Date('2026-07-01T00:00:00Z'))
  })

  it('shifting back from Live leaves Live and anchors one window earlier', () => {
    expect(store.live).toBe(true)
    store.shift(-1)
    expect(store.live).toBe(false)
    expect(store.mode).toBe('last')
    expect(store.range.to).toEqual(new Date(NOW.getTime() - DAY))
    expect(store.range.from).toEqual(new Date(NOW.getTime() - 2 * DAY))
  })

  it('shifting forward while Live is a no-op', () => {
    store.shift(1)
    expect(store.live).toBe(true)
    expect(store.range.to).toEqual(NOW)
  })
})

describe('widen', () => {
  it('doubles an explicit range around its center', () => {
    store.setRange(new Date('2026-07-01T06:00:00Z'), new Date('2026-07-01T12:00:00Z'))
    store.widen()
    expect(store.range.from).toEqual(new Date('2026-07-01T03:00:00Z'))
    expect(store.range.to).toEqual(new Date('2026-07-01T15:00:00Z'))
  })

  it('keeps Live following now, doubling the trailing window', () => {
    store.widen()
    expect(store.live).toBe(true)
    expect(store.windowMs).toBe(2 * DAY)
    expect(store.range.to).toEqual(NOW)
    expect(store.range.from).toEqual(new Date(NOW.getTime() - 2 * DAY))
  })

  it('doubles a frozen last-window around its center', () => {
    store.setPicker({ mode: 'last', anchor: NOW, windowMs: 6 * HOUR, live: false })
    store.widen()
    expect(store.windowMs).toBe(12 * HOUR)
    expect(store.range.to).toEqual(new Date(NOW.getTime() + 3 * HOUR))
    expect(store.range.from).toEqual(new Date(NOW.getTime() - 9 * HOUR))
  })

  it('caps at a year and no-ops once there', () => {
    store.setRange(new Date(NOW.getTime() - 300 * DAY), NOW)
    store.widen()
    expect(store.range.windowMs).toBe(YEAR)
    const capped = store.range
    store.widen()
    expect(store.range).toEqual(capped)
  })
})
