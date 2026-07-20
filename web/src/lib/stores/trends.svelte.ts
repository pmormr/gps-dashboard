/**
 * Trends handoff — a one-shot metric preselection queued by another view (a
 * Sensors sparkline tap) for the Trends view to consume on mount. A module
 * singleton so it survives the client-side navigation; `take()` clears it, so a
 * later plain visit to Trends keeps its own default/preset selection rather than
 * re-opening the last handed-off channel. Mirrors `layers.pendingZoom`.
 */
class TrendsHandoff {
  /** Queued metric addresses (`<sensor_id>.<column>`), or null when nothing is pending. */
  pending = $state<string[] | null>(null)

  /** Queue a metric set for the next Trends mount and hand control to the router. */
  open(metrics: string[]): void {
    this.pending = metrics
  }

  /** Consume and clear the queued selection, returning null if none is pending. */
  take(): string[] | null {
    const p = this.pending
    this.pending = null
    return p
  }
}

export const trendsHandoff = new TrendsHandoff()
