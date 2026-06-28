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
