<script lang="ts">
  import { untrack } from 'svelte'

  import type { MiniMap } from '../lib/map'
  import { drive } from '../lib/stores/drive.svelte'
  import { layers } from '../lib/stores/layers.svelte'

  // The compass: a north-up rose with a heading chevron at the centre, so the
  // direction of travel reads off fixed cardinal letters (the main map is
  // course-up, which loses north). With the mini-map pref on, a second small
  // MapLibre instance (lib/map.ts createMiniMap) renders under the rose at a
  // fixed, user-set zoom, centred on the live position each frame. Off, the
  // rose stands alone on glass — also the fallback if a second WebGL context
  // is too much for the dash phone.
  let {
    lat,
    lon,
    heading,
    size = $bindable(0),
  }: {
    lat: number | null
    lon: number | null
    heading: number | null
    /** The rendered box size (px), bound out so the town ring can avoid it. */
    size?: number
  } = $props()

  let canvasEl = $state<HTMLDivElement | undefined>()
  let mini = $state<MiniMap | null>(null)

  // Create with the canvas element (present only while the pref is on) and
  // destroy with it. The engine chunk is already loaded by Drive; this import
  // resolves to the same cached module. The initial zoom is read untracked —
  // zoom changes go through setZoom below, not a rebuild.
  $effect(() => {
    const el = canvasEl
    if (!el) return
    let cancelled = false
    let created: MiniMap | null = null
    import('../lib/map').then((mod) => {
      if (cancelled) return
      created = mod.createMiniMap(el, untrack(() => drive.miniMapZoom))
      mini = created
    })
    return () => {
      cancelled = true
      created?.destroy()
      mini = null
    }
  })

  $effect(() => {
    mini?.setZoom(drive.miniMapZoom)
  })

  $effect(() => {
    mini?.setBasemap(layers.base, layers.theme)
  })

  $effect(() => {
    if (mini && lat != null && lon != null) mini.setCenter(lat, lon)
  })
</script>

<div class="drive-mini" class:drive-mini--map={drive.miniMap} bind:clientWidth={size}>
  {#if drive.miniMap}
    <div class="drive-mini-canvas" bind:this={canvasEl}></div>
  {/if}
  <div class="drive-mini-rose" aria-hidden="true">
    <span class="n">N</span>
    <span class="e">E</span>
    <span class="s">S</span>
    <span class="w">W</span>
  </div>
  <!-- No transform transition: heading wraps 359→0 and would spin the long way. -->
  <div
    class="drive-mini-chevron"
    class:drive-mini-chevron--none={heading == null}
    style:transform={`translate(-50%, -50%) rotate(${heading ?? 0}deg)`}
    role="img"
    aria-label={heading == null ? 'Heading unknown' : `Heading ${Math.round(heading)}°`}
  ></div>
</div>
