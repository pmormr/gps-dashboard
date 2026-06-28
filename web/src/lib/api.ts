// Typed client for the Van OS JSON API. One function per endpoint; shapes mirror
// the Flask routes (api/routes/*). Same-origin in production; proxied to Flask in dev.

export interface LocationReading {
  timestamp: string
  lat: number
  lon: number
  speed: number | null
  mode: number | null
}

export interface GnssReading {
  timestamp: string
  nsat_used: number | null
  nsat_seen: number | null
  hdop: number | null
  pdop: number | null
}

export interface HouseReading {
  timestamp: string
  battery_soc: number | null
  battery_power: number | null
  pv_power: number | null
  dc_system_power: number | null
}

export interface CabinReading {
  timestamp: string
  temp_c: number | null
  humidity_pct: number | null
  iaq: number | null
}

export interface VanReading {
  timestamp: string
  rpm: number | null
  coolant_c: number | null
  speed_kph: number | null
}

export interface ServiceState {
  name: string
  state: string
}

/** The /api/status Home aggregate; any domain is null when it has no readings yet. */
export interface Status {
  now: string
  location: LocationReading | null
  gnss: GnssReading | null
  house: HouseReading | null
  cabin: CabinReading | null
  van: VanReading | null
  services: ServiceState[]
  ntp: { synced: boolean | null } | null
}

async function getJSON<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`${url} → ${resp.status}`)
  return resp.json() as Promise<T>
}

/** Fetch the Home status aggregate. */
export function getStatus(): Promise<Status> {
  return getJSON<Status>('/api/status')
}

/** A PASS/FAIL diagnostic check, shared by the NTP and gpsd status views. */
export interface StatusCheck {
  name: string
  ok: boolean
}

export interface NtpTracking {
  reference: string | null
  synced: boolean
  stratum: number | null
  offset_ms: number | null
  offset_dir: string | null
  rms_ms: number | null
  leap_status: string | null
}

export interface NtpSource {
  type: string
  selected: boolean
  name: string
  stratum: number
  sample: string
}

export interface NtpStatus {
  overall_ok: boolean
  checks: StatusCheck[]
  service_state: string
  tracking: NtpTracking
  sources: NtpSource[]
  gps_source: NtpSource | null
  pps_source: NtpSource | null
  pps_mode: boolean
  serving: boolean
  conflicts: string[]
}

/** Fetch NTP/chrony status. */
export function getNtp(): Promise<NtpStatus> {
  return getJSON<NtpStatus>('/api/ntp')
}

export interface GpsdLatest {
  timestamp: string
  lat: number
  lon: number
  speed: number | null
  altitude: number | null
}

export interface GpsdStatus {
  overall_ok: boolean
  checks: StatusCheck[]
  service_state: string
  device: string
  device_present: boolean
  fix_mode: number
  fix_label: string
  sats_used: number
  sats_visible: number
  latest: GpsdLatest | null
  data_age: number | null
  frozen: boolean
}

/** Fetch gpsd/receiver status. */
export function getGpsdStatus(): Promise<GpsdStatus> {
  return getJSON<GpsdStatus>('/api/gpsd/status')
}

/** Per-metric presentation metadata, mirrored from api/sensor_schema.py METRIC_META. */
export interface MetricMeta {
  label: string
  unit: string
  dec: number
  chart: boolean
  color: string
  convert: string | null
  y_range: number[] | null
  group: string
}

export interface ReadingTableSpec {
  table: string
  metrics: string[]
}

export interface SensorLatest {
  timestamp: string
  [metric: string]: number | string | null
}

export interface SensorRow {
  id: number
  node: string
  type: string
  location: string | null
  description: string
  first_seen: string
  last_seen: string | null
  status: string
  latest: SensorLatest | null
}

/** The /api/sensors registry: rows with embedded latest readings + presentation meta. */
export interface SensorsResponse {
  sensors: SensorRow[]
  metrics: Record<string, ReadingTableSpec>
  meta: Record<string, MetricMeta>
}

/** Fetch the sensor registry (each row with its latest reading embedded). */
export function getSensors(): Promise<SensorsResponse> {
  return getJSON<SensorsResponse>('/api/sensors')
}

export interface SatPass {
  gnssid: number
  svid: number
  name: string
  system: string
  rise_unix: number
  rise_az: number
  peak_unix: number
  peak_az: number
  peak_el: number
  set_unix: number
  set_az: number
  duration_s: number
  in_progress: boolean
  max_snr: number | null
  used: boolean
}

export interface PassesResponse {
  observer: { lat: number; lon: number; alt: number; timestamp: string }
  generated: string
  horizon_hours: number
  mask_deg: number
  fit_window_hours: number
  counts: { observed: number; fit: number }
  passes: SatPass[]
}

/** Fetch predicted satellite passes for the given horizon (h) and mask (deg). */
export function getPasses(hours: number, mask: number): Promise<PassesResponse> {
  return getJSON<PassesResponse>(`/api/passes?hours=${hours}&mask=${mask}`)
}
