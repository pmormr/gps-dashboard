// Typed client for the Van OS JSON API. One function per endpoint; shapes mirror
// the Flask routes (api/routes/*). Same-origin in production; proxied to Flask in dev.

import type { DocsTree } from './docs'
import type { TrackPoint } from './geo'
import type { DroneFlight } from './map'

export interface LocationReading {
  timestamp: string
  lat: number
  lon: number
  speed: number | null
  altitude: number | null
  track: number | null
  mode: number | null
}

export interface GnssReading {
  timestamp: string
  nsat_used: number | null
  nsat_seen: number | null
  hdop: number | null
  vdop: number | null
  pdop: number | null
}

export interface HouseReading {
  timestamp: string
  battery_soc: number | null
  battery_voltage: number | null
  battery_current: number | null
  battery_power: number | null
  battery_state: number | null
  time_to_go_s: number | null
  pv_power: number | null
  pv_yield_today_kwh: number | null
  solar_state: number | null
  dc_system_power: number | null
  ac_consumption_power: number | null
}

export interface CabinReading {
  timestamp: string
  temp_c: number | null
  humidity_pct: number | null
  dew_point_c: number | null
  pressure_hpa: number | null
  iaq: number | null
  co2_equivalent: number | null
}

export interface VanReading {
  timestamp: string
  rpm: number | null
  speed_kph: number | null
  coolant_c: number | null
  engine_load_pct: number | null
  fuel_level_pct: number | null
  voltage_v: number | null
  ambient_air_c: number | null
  /** Derived server-side at read time (speed density, common/obd.py). */
  fuel_rate_lph: number | null
}

export interface PiReading {
  timestamp: string
  cpu_temp_c: number | null
  load_1m: number | null
  mem_used_pct: number | null
  disk_root_pct: number | null
  disk_nvme_pct: number | null
  disk_nvme_free_gb: number | null
  uptime_s: number | null
  /** vcgencmd get_throttled bitmask; 0 = healthy. */
  throttled: number | null
}

export interface RouterReading {
  timestamp: string
  wan_up: number | null
  wan_ping_ms: number | null
  wan_rx_kbps: number | null
  wan_tx_kbps: number | null
  halow_rssi_dbm: number | null
  halow_stations: number | null
  halow_rx_mbps: number | null
  halow_tx_mbps: number | null
  halow_temp_c: number | null
  dhcp_leases: number | null
  uptime_s: number | null
}

export interface NvrReading {
  timestamp: string
  hdd_ok: number | null
  hdd_temp_c: number | null
  channels_video_loss: number | null
  cpu_pct: number | null
  mem_used_pct: number | null
  clock_offset_s: number | null
}

/** Camera-fleet aggregate — Home never shows per-camera detail. */
export interface CamerasAggregate {
  /** Oldest of the per-camera latest readings — stale if any camera stream stops. */
  timestamp: string
  online: number
  total: number
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
  /** OBD stream health from the sensors registry: online / no_adapter / no_car /
   * offline (reader down) / unknown; null when the stream never registered. */
  obd_link: string | null
  pi: PiReading | null
  router: RouterReading | null
  nvr: NvrReading | null
  cameras: CamerasAggregate | null
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

/** One live fix from `/api/gpsd/live` — the Drive view's 1 Hz poll payload. */
export interface LiveFix {
  connected: boolean
  mode: number
  fix_label: string
  lat: number | null
  lon: number | null
  /** Doppler ground speed, m/s. */
  speed: number | null
  /** Course over ground, degrees true — noise below ~1 m/s (heading gate). */
  track: number | null
  /** Altitude (MSL preferred), metres. */
  alt: number | null
  /** Vertical speed, m/s. */
  climb: number | null
  time: string | null
  /** Fix age computed server-side, seconds — no client/Pi clock agreement needed. */
  fix_age_s: number | null
}

/** Fetch the freshest gpsd fix (TPV-only snapshot; Drive's live feed). */
export function getGpsdLive(): Promise<LiveFix> {
  return getJSON<LiveFix>('/api/gpsd/live')
}

/** One raw-tier trailing-window point (`/api/points/recent`, Drive breadcrumb seed). */
export interface RecentPoint {
  timestamp: string
  lat: number
  lon: number
}

export interface RecentPointsResponse {
  points: RecentPoint[]
  count: number
  raw_count: number
  minutes: number
}

/** Fetch the raw trailing window, stride-decimated (Drive breadcrumb seed). */
export function getPointsRecent(minutes: number, limit: number): Promise<RecentPointsResponse> {
  return getJSON<RecentPointsResponse>(`/api/points/recent?minutes=${minutes}&limit=${limit}`)
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
  /** Default smoothing window (moving-average buckets); 0 = none. A floor, not a cap. */
  smooth: number
  /** How an integer-coded column decodes for display: 'enum', 'bitmask', or null. */
  codec: string | null
  /** code→label table for `codec` (string keys over JSON): value→label for 'enum',
   * mask→label for 'bitmask' (the '0' key is the all-clear label). Null when codec is null. */
  codes: Record<string, string> | null
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

/** One bucketed metric series: METRIC_META presentation fields + an aligned value array. */
export interface TrendSeries {
  metric: string
  sensor_id: number
  column: string
  label: string
  unit: string
  color: string
  dec: number
  convert: string | null
  y_range: number[] | null
  group: string
  /** Default smoothing window (moving-average buckets) for this channel; 0 = none. */
  smooth: number
  /** Averaged value per bucket, aligned to the shared `x`; null for empty buckets. */
  values: (number | null)[]
  /** Per-bucket min/max (raw spread), aligned to `x`, for the optional envelope band. */
  min: (number | null)[]
  max: (number | null)[]
}

/** `/api/sensors/series`: a shared bucket grid (`x`, epoch ms) + overlaid metric series. */
export interface SensorSeriesResponse {
  start: string
  end: string
  bucket_ms: number
  x: number[]
  series: TrendSeries[]
}

/** Fetch bucketed, aligned trend series for overlaid metrics over a window. */
export function getSensorSeries(
  metrics: string[],
  start: string,
  end: string,
  buckets?: number
): Promise<SensorSeriesResponse> {
  const params = new URLSearchParams({ metrics: metrics.join(','), start, end })
  if (buckets != null) params.set('buckets', String(buckets))
  return getJSON<SensorSeriesResponse>(`/api/sensors/series?${params}`)
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

/** Live ID-5100A main-band state; online:false (with service state) when rigctld is down. */
export interface RadioStatus {
  online: boolean
  service?: string
  error?: string
  freq_hz?: number
  mode?: string
  passband_hz?: number
  rawstr?: number | null
  levels?: { af?: number | null; sql?: number | null; rfpower?: number | null }
  tone_mode?: string
  ctcss_tone_hz?: number | null
  rptr_shift?: string
  rptr_offset_hz?: number | null
  dcd?: boolean
  ptt?: boolean
}

/** Fetch live radio state (always 200; check `online`). */
export function getRadioStatus(): Promise<RadioStatus> {
  return getJSON<RadioStatus>('/api/radio/status')
}

/** POST a radio control write; throws Error(message) on a rig/daemon refusal. */
// ── Points (the map trail, processed track_points tier) ──

/** `/api/points` response: size-aware decimated track points + a truncation flag. */
export interface PointsResponse {
  points: TrackPoint[]
  truncated?: boolean
}

/** Trail/history for a time window (stops always kept; moving thinned by importance). */
export function getPoints(
  start: string,
  end: string,
  limit = 5000,
  bbox?: string | null,
): Promise<PointsResponse> {
  const params = new URLSearchParams({ start, end, limit: String(limit) })
  if (bbox) params.set('bbox', bbox)
  return getJSON<PointsResponse>(`/api/points?${params}`)
}

/** The single most-recent raw fix (live position). */
export function getPointsLatest(): Promise<TrackPoint | null> {
  return getJSON<TrackPoint | null>('/api/points/latest')
}

export async function postRadio(path: string, body: unknown): Promise<void> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const data = (await resp.json().catch(() => ({}))) as { error?: string }
    throw new Error(data.error ?? `Failed (${resp.status})`)
  }
}

// ── Annotations + marks (map view) ──

/** A user-curated annotation. `end_time` null ⇒ point bookmark; else a range. */
export interface Annotation {
  id: number
  name: string
  notes: string | null
  start_time: string
  end_time: string | null
  point_count: number | null
}

/** The fields accepted when creating an annotation (range omits/nulls `end_time`). */
export interface NewAnnotation {
  name: string
  start_time: string
  end_time?: string | null
  notes?: string
}

/** Send a JSON body (or none) and parse the reply; throws Error(message) on failure. */
async function sendJSON<T>(url: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (resp.status === 204) return undefined as T
  const data = (await resp.json().catch(() => ({}))) as { error?: string }
  if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`)
  return data as T
}

/** List every annotation (all history; the frontend windows them client-side). */
export function getAnnotations(): Promise<{ annotations: Annotation[] }> {
  return getJSON<{ annotations: Annotation[] }>('/api/annotations')
}

/** Create an annotation (point bookmark or range); returns the stored row. */
export function createAnnotation(data: NewAnnotation): Promise<Annotation> {
  return sendJSON<Annotation>('/api/annotations', 'POST', data)
}

/** Patch an annotation's name/notes/bounds; returns the updated row. */
export function updateAnnotation(
  id: number,
  data: { name?: string; notes?: string; start_time?: string; end_time?: string | null },
): Promise<Annotation> {
  return sendJSON<Annotation>(`/api/annotations/${id}`, 'PATCH', data)
}

/** Delete an annotation. */
export function deleteAnnotation(id: number): Promise<void> {
  return sendJSON<void>(`/api/annotations/${id}`, 'DELETE')
}

/** The persisted live range-construction marks (either key may be absent). */
export interface Marks {
  start?: string
  end?: string
}

/** Read the persisted start/end marks. */
export function getMarks(): Promise<Marks> {
  return getJSON<Marks>('/api/annotations/mark')
}

/** Stamp the given mark with the current UTC time; returns the full mark set. */
export function markTimestamp(marker: 'start' | 'end'): Promise<Marks> {
  return sendJSON<Marks>('/api/annotations/mark', 'POST', { marker })
}

/** Per-window drive summary (derived OBD fuel ÷ GPS-track distance + speed/time). */
export interface ObdEconomy {
  mpg: number | null
  distance_mi: number
  fuel_gallons: number
  max_speed_kph: number | null
  engine_on_s: number
  moving_s: number
  idle_s: number
  n_obd_rows: number
  n_track_points: number
  calibrated: boolean
}

/** Fetch fuel economy over a window (pass an annotation's bounds for per-trip MPG). */
export function getObdEconomy(start: string, end: string): Promise<ObdEconomy> {
  const params = new URLSearchParams({ start, end })
  return getJSON<ObdEconomy>(`/api/obd/economy?${params}`)
}

/** All drone flights with their thinned tracks embedded (the map-overlay read). */
export function getDroneFlights(): Promise<{ flights: DroneFlight[] }> {
  return getJSON<{ flights: DroneFlight[] }>('/api/drone/flights')
}

// ── Phone location history (Google Timeline import) ──

/** One breadcrumb vertex; `activity_type` is the covering trip mode (color-by-mode). */
export interface PhonePoint {
  lat: number
  lon: number
  importance: number
  activity_type: string | null
}

/** A breadcrumb path (one contiguous Timeline segment) with its thinned points. */
export interface PhonePath {
  id: number
  start_time: string
  end_time: string
  n_points: number
  points?: PhonePoint[]
}

/** A place visit from the semantic layer. */
export interface PhoneVisit {
  id: number
  start_time: string
  end_time: string
  lat: number
  lon: number
  place_id: string | null
  semantic_type: string | null
  probability: number | null
}

export interface PhoneTracksResponse {
  paths: PhonePath[]
  truncated?: boolean
}

export interface PhonePlacesResponse {
  visits: PhoneVisit[]
  activities: unknown[]
  truncated?: boolean
}

/** Breadcrumb paths overlapping the window, thinned points embedded. */
export function getPhoneTracks(start: string, end: string): Promise<PhoneTracksResponse> {
  const params = new URLSearchParams({ start, end })
  return getJSON<PhoneTracksResponse>(`/api/phone/tracks?${params}`)
}

/** Place visits (+ activities) overlapping the window. */
export function getPhonePlaces(start: string, end: string): Promise<PhonePlacesResponse> {
  const params = new URLSearchParams({ start, end })
  return getJSON<PhonePlacesResponse>(`/api/phone/places?${params}`)
}

// ── Attractions (parks/public-lands POI tier, synced from the NPS API + RIDB export) ──

export type AttractionKind =
  | 'park'
  | 'thingstodo'
  | 'tour'
  | 'visitorcenter'
  | 'campground'
  // RIDB (recreation.gov) kinds — the other federal agencies (FS/USACE/BLM/FWS…):
  | 'recarea' // the park-analog container (forest, lake project, refuge)
  | 'facility' // the generic place type: trailheads, cabins, boat ramps, day-use sites
  | 'permit' // permit/timed-entry requirements, kept as planning signals

/** One POI list row (queryable columns + summary teaser; details fetched per-row). */
export interface Attraction {
  id: number
  source: string
  source_kind: AttractionKind
  source_id: string
  park_code: string | null
  name: string
  /** Null for the few rows whose kind and park both lack a coordinate. */
  lat: number | null
  lon: number | null
  summary: string | null
  /** When this row was last synced from its source — the UI wears this age. */
  synced_at: string
}

/** A self-guided tour stop; lat/lon joined from the NPS `places` asset at import. */
export interface TourStop {
  ordinal: number
  assetName: string
  significance: string | null
  audioTranscript: string | null
  audioFileUrl: string | null
  directionsToNextStop: string | null
  lat: number | null
  lon: number | null
}

/** One facility location's hours: a weekday→text map plus dated exceptions. */
export interface OperatingHours {
  name?: string
  description?: string
  standardHours?: Record<string, string>
  exceptions?: {
    name?: string
    startDate?: string
    endDate?: string
    exceptionHours?: Record<string, string>
  }[]
}

/**
 * The trimmed source record (display-only structure). Kind-specific keys pass
 * through as published — only the fields the detail sheet renders are typed.
 */
export interface AttractionDetails {
  description?: string
  operatingHours?: OperatingHours[]
  amenities?: string[]
  fees?: { cost?: string; title?: string; description?: string }[]
  stops?: TourStop[]
  durationMin?: string
  durationMax?: string
  durationUnit?: string
  // RIDB fields (normalized at import; all optional — NPS rows never carry them):
  directions?: string
  feeText?: string
  phone?: string
  org?: string
  recAreaName?: string
  city?: string
  state?: string
  reservable?: boolean
  stayLimit?: string
  activities?: string[]
  /** Per-campground aggregate; equipment max lengths in feet where length matters. */
  campsites?: { count: number; equipment: { name: string; maxLengthFt?: number }[] }
  [key: string]: unknown
}

export interface AttractionsResponse {
  attractions: Attraction[]
  count: number
  truncated: boolean
}

/** One expanded event occurrence; park-local calendar date, not the ms-UTC axis. */
export interface EventOccurrence {
  date: string
  time_start: string | null
  time_end: string | null
}

/** An event with its matching occurrences (SQLite booleans arrive as 0/1). */
export interface AttractionEvent {
  id: number
  source: string
  source_id: string
  park_code: string | null
  name: string
  lat: number | null
  lon: number | null
  location_text: string | null
  is_free: number | null
  needs_reservation: number | null
  synced_at: string
  dates: EventOccurrence[]
}

export interface AttractionEventsResponse {
  events: AttractionEvent[]
  count: number
  truncated: boolean
}

/** POI list; all filters optional (kinds joined comma-separated, `q` = name substring). */
export function getAttractions(opts: {
  bbox?: string
  kinds?: AttractionKind[]
  park?: string
  q?: string
  limit?: number
}): Promise<AttractionsResponse> {
  const params = new URLSearchParams()
  if (opts.bbox) params.set('bbox', opts.bbox)
  if (opts.kinds?.length) params.set('kind', opts.kinds.join(','))
  if (opts.park) params.set('park', opts.park)
  if (opts.q) params.set('q', opts.q)
  if (opts.limit != null) params.set('limit', String(opts.limit))
  return getJSON<AttractionsResponse>(`/api/attractions?${params}`)
}

/** One POI with its full parsed details (tour stops, hours, amenities, fees). */
export function getAttraction(id: number): Promise<Attraction & { details: AttractionDetails }> {
  return getJSON<Attraction & { details: AttractionDetails }>(`/api/attractions/${id}`)
}

/** One event with parsed details and its full occurrence list. */
export function getAttractionEvent(
  id: number,
): Promise<AttractionEvent & { details: Record<string, unknown> }> {
  return getJSON<AttractionEvent & { details: Record<string, unknown> }>(
    `/api/attractions/events/${id}`,
  )
}

/** Event occurrences in a calendar-date window (`YYYY-MM-DD`), grouped per event. */
export function getAttractionEvents(opts: {
  start?: string
  end?: string
  bbox?: string
  park?: string
  limit?: number
}): Promise<AttractionEventsResponse> {
  const params = new URLSearchParams()
  if (opts.start) params.set('start', opts.start)
  if (opts.end) params.set('end', opts.end)
  if (opts.bbox) params.set('bbox', opts.bbox)
  if (opts.park) params.set('park', opts.park)
  if (opts.limit != null) params.set('limit', String(opts.limit))
  return getJSON<AttractionEventsResponse>(`/api/attractions/events?${params}`)
}

/** Fetch the network-docs file tree (empty when the vault is unconfigured). */
export function getDocsTree(): Promise<DocsTree> {
  return getJSON<DocsTree>('/api/docs/tree')
}

/** One vault file's raw markdown plus the ETag the editor echoes back on save. */
export interface DocFile {
  content: string
  etag: string
}

/** Fetch one vault file's raw markdown body (+ content-hash ETag). */
export async function getDocFile(path: string): Promise<DocFile> {
  const resp = await fetch(`/api/docs/file?path=${encodeURIComponent(path)}`)
  if (!resp.ok) throw new Error(`/api/docs/file → ${resp.status}`)
  return { content: await resp.text(), etag: resp.headers.get('ETag') ?? '' }
}

/** Result of saving a vault file: whether the edit landed in a git commit. */
export interface DocSaveResult {
  committed: boolean
  etag: string
}

/**
 * Overwrite one existing vault file (edit-only; auto-committed server-side).
 * Throws `'conflict'` on a stale ETag — the file changed since it was loaded.
 */
export async function putDocFile(
  path: string,
  content: string,
  etag: string,
): Promise<DocSaveResult> {
  const resp = await fetch(`/api/docs/file?path=${encodeURIComponent(path)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'text/markdown', 'If-Match': etag },
    body: content,
  })
  if (resp.status === 409) throw new Error('conflict')
  if (!resp.ok) throw new Error(`/api/docs/file → ${resp.status}`)
  const body = (await resp.json()) as { committed: boolean }
  return { committed: body.committed, etag: resp.headers.get('ETag') ?? '' }
}
