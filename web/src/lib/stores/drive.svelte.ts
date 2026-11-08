/**
 * The Drive store — the Drive HUD's user-tunable knobs (town-compass ring cap
 * and label inset, the mini-map, the follow camera's speed→zoom curve), a
 * module singleton so they persist across route remounts. Seeds from the
 * persisted prefs (`drivePrefs.ts`) and writes every change back from one
 * root effect, so no panel handler can forget to persist.
 */

import { type DrivePrefs, loadDrivePrefs, saveDrivePrefs } from '../drivePrefs'

const boot = loadDrivePrefs()

class DriveStore {
  ringCap = $state(boot.ringCap)
  labelInset = $state(boot.labelInset)
  miniMap = $state(boot.miniMap)
  miniMapZoom = $state(boot.miniMapZoom)
  zoomMin = $state(boot.zoomMin)
  zoomHiMph = $state(boot.zoomHiMph)
  zoomSlew = $state(boot.zoomSlew)

  /** The persisted slice (drivePrefs.ts) — reading it tracks every field. */
  get prefs(): DrivePrefs {
    return {
      ringCap: this.ringCap,
      labelInset: this.labelInset,
      miniMap: this.miniMap,
      miniMapZoom: this.miniMapZoom,
      zoomMin: this.zoomMin,
      zoomHiMph: this.zoomHiMph,
      zoomSlew: this.zoomSlew,
    }
  }
}

export const drive = new DriveStore()

$effect.root(() => {
  $effect(() => {
    saveDrivePrefs(drive.prefs)
  })
})
