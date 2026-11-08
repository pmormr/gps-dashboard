<script lang="ts">
  import { LABEL_INSET_RANGE, RING_CAP_RANGE } from '../lib/drivePrefs'
  import { drive } from '../lib/stores/drive.svelte'
  import './layers.css'

  // The Drive-HUD rail-panel content: the town-compass density knobs, bound
  // to the `drive` store (which persists them). Body-only — the rail owns
  // open/close. Changes apply on the compass's next selection/frame.
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
</div>
