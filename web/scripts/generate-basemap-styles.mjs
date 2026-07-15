/**
 * Basemap style generator — emits the vendored MapLibre style documents
 * (static/vendor/basemap/style-<flavor>.json) from @protomaps/basemaps.
 *
 * Dev-time only (the emitted JSON is committed; the Pi never runs this).
 * Regenerate after bumping @protomaps/basemaps — but the package's style
 * output must target the tile schema version of the served archive
 * (northamerica.pmtiles, planetiler tileset v4.x; check `pmtiles show`).
 * The runtime addresses layers by id (labels.ts: `pois`,
 * `roads_labels_minor`; map.ts label scaling walks all symbol layers), so
 * verify ids survive a regeneration: `jq -r '.layers[].id'` diff old vs new.
 *
 * Run: `npm run gen:styles` (from web/).
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { layers, namedFlavor } from '@protomaps/basemaps'

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), '../../static/vendor/basemap')

/** The shipped theme set: every built-in @protomaps/basemaps flavor. */
const FLAVORS = ['light', 'dark', 'white', 'grayscale', 'black']

/**
 * Default-sprite variant per flavor (shields, oneway arrows, townspots —
 * the pois layer's icons are overridden at runtime to the SDF poi sprite).
 * Both variants are vendored under static/vendor/basemap/sprite/.
 */
const SPRITE_VARIANT = { light: 'light', dark: 'dark', white: 'light', grayscale: 'light', black: 'dark' }

/**
 * Local overrides applied to every flavor's generated layers, keyed by
 * layer id. Upstream ships fixed 12 px road names at every zoom (and 8 px
 * shields) — too small on a phone in a moving van. Sizes are
 * zoom-interpolated so Drive's label-scale multiplier (map.ts
 * scaleSizeValue) still composes with them.
 */
const PATCHES = {
  // Major road names: readable floor, growing with zoom; Medium face for
  // extra weight (glyphs already vendored).
  roads_labels_major: (l) => {
    l.layout['text-size'] = ['interpolate', ['linear'], ['zoom'], 11, 13, 16, 16, 19, 20]
    l.layout['text-font'] = ['Noto Sans Medium']
  },
  // Minor road/path names: same treatment, one step smaller.
  roads_labels_minor: (l) => {
    l.layout['text-size'] = ['interpolate', ['linear'], ['zoom'], 13, 12, 16, 14.5, 19, 18]
  },
  // Highway shields: text and shield image scale together (the shield PNG
  // is picked by text length, so the pair must keep their ratio).
  roads_shields: (l) => {
    l.layout['text-size'] = 9.2
    l.layout['icon-size'] = 1.15
  },
  // POI mark labels: upstream holds 10 px until z17. The density slider
  // pulls marks well below that, so give them a readable floor early.
  pois: (l) => {
    l.layout['text-size'] = ['interpolate', ['linear'], ['zoom'], 13, 11, 17, 12.5, 19, 16]
  },
}

/** Build one flavor's complete style document (root-relative asset URLs; map.ts absolutizes). */
function styleDoc(name) {
  const styleLayers = layers('protomaps', namedFlavor(name), { lang: 'en' })
  for (const layer of styleLayers) {
    const patch = PATCHES[layer.id]
    if (patch) patch(layer)
  }
  return {
    version: 8,
    name: `protomaps-${name}`,
    sources: {
      protomaps: {
        type: 'vector',
        url: 'pmtiles:///tiles/osm.pmtiles',
        attribution: '© OpenStreetMap',
      },
    },
    sprite: `/static/vendor/basemap/sprite/${SPRITE_VARIANT[name]}`,
    glyphs: '/static/vendor/basemap/glyphs/{fontstack}/{range}.pbf',
    layers: styleLayers,
  }
}

mkdirSync(OUT_DIR, { recursive: true })
for (const name of FLAVORS) {
  const path = join(OUT_DIR, `style-${name}.json`)
  writeFileSync(path, JSON.stringify(styleDoc(name)))
  console.log(`wrote ${path}`)
}
