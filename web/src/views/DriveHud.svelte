<script lang="ts">
  import {
    LABEL_INSET_RANGE,
    MINI_MAP_ZOOM_RANGE,
    RING_CAP_RANGE,
    ZOOM_HI_MPH_RANGE,
    ZOOM_MIN_RANGE,
    ZOOM_SLEW_RANGE,
  } from '../lib/drivePrefs'
  import { drive } from '../lib/stores/drive.svelte'
  import './layers.css'

  // The Drive-HUD rail-panel content: the town-compass density knobs, the
  // compass mini-map, and the follow camera's speed→zoom curve, bound to the
  // `drive` store (which persists them). Body-only — the rail owns
  // open/close. Changes apply on the next selection/frame.
</script>

<div class="layers-panel">
  <div class="layers-section">
    <h4>Town compass</h4>
    <div class="label-row">
      <h4>Ring marks <span class="label-val">{drive.ringCap}</span></h4>
      <input
        type="range"
        min={RING_CAP_RANGE.min}
        max={RING_CAP_RANGE.max}
        step="1"
        bind:value={drive.ringCap}
      />
      <div class="label-hint">how many towns the ring shows at once</div>
    </div>
    <div class="label-row">
      <h4>Edge inset <span class="label-val">{drive.labelInset}px</span></h4>
      <input
        type="range"
        min={LABEL_INSET_RANGE.min}
        max={LABEL_INSET_RANGE.max}
        step="2"
        bind:value={drive.labelInset}
      />
      <div class="label-hint">gap between the screen edge and the labels</div>
    </div>
  </div>

  <div class="layers-section">
    <h4>Compass</h4>
    <label class="label-check">
      <input type="checkbox" bind:checked={drive.miniMap} /> Live mini-map under the rose
    </label>
    {#if drive.miniMap}
      <div class="label-row">
        <h4>Mini-map zoom <span class="label-val">z{drive.miniMapZoom}</span></h4>
        <input
          type="range"
          min={MINI_MAP_ZOOM_RANGE.min}
          max={MINI_MAP_ZOOM_RANGE.max}
          step="1"
          bind:value={drive.miniMapZoom}
        />
        <div class="label-hint">regional overview — lower shows more states</div>
      </div>
    {/if}
  </div>

  <div class="layers-section">
    <h4>Follow camera</h4>
    <div class="label-row">
      <h4>Highway zoom <span class="label-val">z{drive.zoomMin.toFixed(1)}</span></h4>
      <input
        type="range"
        min={ZOOM_MIN_RANGE.min}
        max={ZOOM_MIN_RANGE.max}
        step={ZOOM_MIN_RANGE.step}
        bind:value={drive.zoomMin}
      />
      <div class="label-hint">how far out the map sits at full speed</div>
    </div>
    <div class="label-row">
      <h4>Full zoom-out at <span class="label-val">{drive.zoomHiMph} mph</span></h4>
      <input
        type="range"
        min={ZOOM_HI_MPH_RANGE.min}
        max={ZOOM_HI_MPH_RANGE.max}
        step="5"
        bind:value={drive.zoomHiMph}
      />
      <div class="label-hint">lower pulls the map back sooner as you get moving</div>
    </div>
    <div class="label-row">
      <h4>Zoom ramp <span class="label-val">{drive.zoomSlew.toFixed(1)} z/s</span></h4>
      <input
        type="range"
        min={ZOOM_SLEW_RANGE.min}
        max={ZOOM_SLEW_RANGE.max}
        step={ZOOM_SLEW_RANGE.step}
        bind:value={drive.zoomSlew}
      />
      <div class="label-hint">fastest the camera may zoom per second</div>
    </div>
  </div>
</div>
