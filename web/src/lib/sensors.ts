// Pure presentation helpers for the sensor registry. The server
// (api/sensor_schema.py METRIC_META) owns labels, units, decimals, grouping, and
// alt-unit conversions; these just apply them.

import type { MetricMeta, SensorRow, SensorsResponse } from './api'

const FALLBACK_META: MetricMeta = {
  label: '',
  unit: '',
  dec: 1,
  chart: true,
  color: '#94a3b8',
  convert: null,
  y_range: null,
  group: '',
  smooth: 0,
}

/** Display metadata for a metric column, falling back for unknown columns. */
export function metricMeta(meta: Record<string, MetricMeta>, key: string): MetricMeta {
  return meta[key] ?? { ...FALLBACK_META, label: key }
}

const CONVERTERS: Record<string, (v: number) => { value: number; unit: string }> = {
  c_to_f: (v) => ({ value: (v * 9) / 5 + 32, unit: '°F' }),
  kph_to_mph: (v) => ({ value: v * 0.621371, unit: 'mph' }),
  s_to_h: (v) => ({ value: v / 3600, unit: 'h' }),
}

const GROUP_LABELS: Record<string, string> = {
  engine: 'Engine',
  temps: 'Temperatures',
  fuel: 'Fuel',
  electrical: 'Electrical',
  environment: 'Environment',
  battery: 'Battery',
  solar: 'Solar',
  dc: 'DC',
  ac: 'AC',
}

/** Human label for a metric-group key, capitalized as a fallback. */
export function groupLabel(group: string): string {
  return GROUP_LABELS[group] ?? group.charAt(0).toUpperCase() + group.slice(1)
}

/** Ordered ``[group, keys[]]`` pairs, preserving first-seen group + within-group order. */
export function groupedKeys(
  meta: Record<string, MetricMeta>,
  keys: string[]
): [string, string[]][] {
  const order: string[] = []
  const byGroup = new Map<string, string[]>()
  for (const key of keys) {
    const g = metricMeta(meta, key).group || ''
    if (!byGroup.has(g)) {
      byGroup.set(g, [])
      order.push(g)
    }
    byGroup.get(g)!.push(key)
  }
  return order.map((g) => [g, byGroup.get(g)!])
}

/** Ordered metric keys to display for a sensor, from the server's type map. */
export function metricKeysFor(resp: SensorsResponse, sensor: SensorRow): string[] {
  const spec = resp.metrics[sensor.type]
  if (spec) return spec.metrics
  return sensor.latest ? Object.keys(sensor.latest).filter((k) => k !== 'timestamp') : []
}

export interface FormattedValue {
  text: string
  unit: string
  alt: string | null
}

/** Format one metric value with its unit and optional alt-unit, or an em dash. */
export function formatValue(
  meta: Record<string, MetricMeta>,
  key: string,
  value: number | string | null | undefined
): FormattedValue {
  if (value == null) return { text: '—', unit: '', alt: null }
  const m = metricMeta(meta, key)
  const num = Number(value)
  const conv = m.convert ? CONVERTERS[m.convert] : null
  let alt: string | null = null
  if (conv) {
    const a = conv(num)
    alt = `${a.value.toFixed(m.dec)} ${a.unit}`
  }
  return { text: num.toFixed(m.dec), unit: m.unit, alt }
}

/** Seconds between a canonical UTC timestamp and now, or null if absent/unparseable. */
export function ageSeconds(ts: string | null): number | null {
  if (!ts) return null
  const ms = Date.parse(ts)
  return Number.isNaN(ms) ? null : (Date.now() - ms) / 1000
}

/** Format an age in seconds as a compact human string. */
export function formatAge(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

/** Liveness class: broker status, downgraded to "warn" when readings have gone stale. */
export function dotClass(sensor: SensorRow): string {
  const age = ageSeconds(sensor.last_seen)
  if (sensor.status === 'offline') return 'err'
  if (sensor.status === 'online') return age != null && age > 90 ? 'warn' : 'ok'
  return 'unknown'
}

/** Friendly section title per sensor type (Van OS domain naming). */
export const DOMAIN_LABELS: Record<string, string> = {
  victron: 'House power',
  obd: 'Van',
  bme680: 'Cabin',
}

const DOMAIN_ORDER = ['victron', 'obd', 'bme680']

/** Sensors in Van OS domain order (House power, Van, Cabin), unknowns last. */
export function orderedSensors(sensors: SensorRow[]): SensorRow[] {
  const rank = (t: string): number => {
    const i = DOMAIN_ORDER.indexOf(t)
    return i < 0 ? DOMAIN_ORDER.length : i
  }
  return [...sensors].sort((a, b) => rank(a.type) - rank(b.type))
}
