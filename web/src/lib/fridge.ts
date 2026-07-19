// Pure helpers for the Fridge view: adapt the stored DC history to the Trend
// chart's series shape, clamp setpoint drafts to the fridge-reported range, and
// format temperatures in the fridge's own presented unit.

import type { FridgeHistoryResponse, FridgeRange, SensorSeriesResponse } from './api'
import { celsiusToF } from './geo'

/** The probed CFX3 75DZ allowed setpoint range — the stepper's bound when the
 * live ranges haven't been fetched yet (fridge off the LAN). */
export const FALLBACK_RANGE: FridgeRange = { min_c: -22, max_c: 10 }

// Matches METRIC_META's dc_current_a color so Trends and the fridge page agree.
const DC_DRAW_COLOR = '#fb923c'

/**
 * Adapt a fridge-history payload to the shape `charts/Trend.svelte` renders,
 * so the chart is pure reuse: one series over the bucket-start time grid.
 */
export function historyToSeries(resp: FridgeHistoryResponse): SensorSeriesResponse {
  const x = resp.points.map((p) => Date.parse(p.ts))
  const nulls = resp.points.map(() => null)
  return {
    start: resp.points[0]?.ts ?? '',
    end: resp.points[resp.points.length - 1]?.ts ?? '',
    bucket_ms: resp.bucket_s * 1000,
    x,
    series: [
      {
        metric: 'fridge.dc_current_a',
        sensor_id: 0,
        column: 'dc_current_a',
        label: 'DC draw',
        unit: 'A',
        color: DC_DRAW_COLOR,
        dec: 1,
        convert: null,
        y_range: null,
        group: 'power',
        smooth: 0,
        values: resp.points.map((p) => p.dc_current_a),
        min: nulls,
        max: nulls,
      },
    ],
  }
}

/** Clamp a setpoint draft to the fridge-reported allowed range (whole degrees). */
export function clampSetpoint(tempC: number, range: FridgeRange | null): number {
  const r = range ?? FALLBACK_RANGE
  return Math.min(r.max_c, Math.max(r.min_c, Math.round(tempC)))
}

/**
 * Format a °C value in the fridge's own presented unit (`temp_unit`: 1 = °F),
 * falling back to °C while the unit is unknown.
 */
export function formatTemp(tempC: number | null | undefined, tempUnit: number | null): string {
  if (tempC == null || typeof tempC !== 'number') return '—'
  if (tempUnit === 1) return `${Math.round(celsiusToF(tempC))}°F`
  return `${tempC.toFixed(1)}°C`
}
