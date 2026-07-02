/**
 * The annotations store — named windows on the time axis (annotations are pure
 * time metadata; any tier replays against the bounds).
 *
 * Holds the annotation list and the create/edit form state, and owns the CRUD +
 * jump-to-annotation flow. The list + jump are axis-level (time-dock Phase 3):
 * the TimeDock's strip bands and the picker's saved-windows section consume them
 * from Map and Trends alike; jumps reframe the *global* window via `selection`.
 * Only the map rendering (pins/range polylines, `pendingPan`) stays map-side, in
 * Map.svelte, which has the loaded points.
 */

import {
  createAnnotation,
  deleteAnnotation,
  getAnnotations,
  getPointsLatest,
  updateAnnotation,
  type Annotation,
} from '../api'
import { selection } from './selection.svelte'

/** The create/edit modal's working state (null = closed). */
export interface FormState {
  editId: number | null // null = create, else the annotation being edited
  title: string
  whenLabel: string
  /** ISO bounds for a create (POST); display-only for an edit. */
  startTime: string
  endTime: string | null
  name: string
  notes: string
}

/** Human "when" line for the form header. */
function fmtWhen(startTime: string, endTime: string | null): string {
  const start = new Date(startTime)
  return endTime
    ? `${start.toLocaleString()} → ${new Date(endTime).toLocaleString()}`
    : `At ${start.toLocaleString()}`
}

class AnnotationsStore {
  list = $state<Annotation[]>([])
  loadError = $state('')
  form = $state<FormState | null>(null)
  // Set when jumping to a *point* annotation: Timeline pans to the nearest fix
  // once the reframed window's points land, then clears it.
  pendingPan = $state<Date | null>(null)

  get count(): number {
    return this.list.length
  }

  /** Reload the list from the server. */
  async reload(): Promise<void> {
    try {
      const data = await getAnnotations()
      this.list = data.annotations
      this.loadError = ''
    } catch (e) {
      this.loadError = e instanceof Error ? e.message : String(e)
    }
  }

  /** Open the form to create a range annotation over the given ISO bounds. */
  openCreate(startTime: string, endTime: string): void {
    this.form = {
      editId: null,
      title: 'Create Range',
      whenLabel: fmtWhen(startTime, endTime),
      startTime,
      endTime,
      name: '',
      notes: '',
    }
  }

  /** Open the form to edit an existing annotation's name + notes (bounds stay put). */
  openEdit(ann: Annotation): void {
    this.form = {
      editId: ann.id,
      title: 'Edit Annotation',
      whenLabel: fmtWhen(ann.start_time, ann.end_time),
      startTime: ann.start_time,
      endTime: ann.end_time,
      name: ann.name ?? '',
      notes: ann.notes ?? '',
    }
  }

  closeForm(): void {
    this.form = null
  }

  /** Persist the open form (POST create / PATCH edit), then reload. Throws on failure. */
  async save(): Promise<void> {
    const f = this.form
    if (!f) return
    const name = f.name.trim()
    if (!name) return
    const notes = f.notes.trim()
    if (f.editId != null) {
      await updateAnnotation(f.editId, { name, notes })
    } else {
      await createAnnotation({ name, start_time: f.startTime, end_time: f.endTime, notes })
    }
    this.closeForm()
    await this.reload()
  }

  /** Delete an annotation, then reload. Throws on failure. */
  async remove(id: number): Promise<void> {
    await deleteAnnotation(id)
    await this.reload()
  }

  /**
   * Reframe the global window so the annotation is in view. Range → an explicit
   * `range` (a saved window restores exactly); point → an explicit range of the
   * current window size centred on its time, with a pending pan so the map
   * recentres once points land.
   */
  jumpTo(ann: Pick<Annotation, 'start_time' | 'end_time'>): void {
    if (ann.end_time) {
      this.pendingPan = null
      selection.setRange(new Date(ann.start_time), new Date(ann.end_time))
    } else {
      const ts = new Date(ann.start_time)
      this.pendingPan = ts
      const half = selection.range.windowMs / 2
      selection.setRange(new Date(ts.getTime() - half), new Date(ts.getTime() + half))
    }
  }

  /**
   * One-tap point bookmark at the latest GPS fix's time (so it lands on the true
   * current position), auto-named and form-less — usable while driving. Reloads
   * on success; throws on failure.
   */
  async bookmarkCurrent(): Promise<void> {
    let ts: string
    try {
      const pt = await getPointsLatest()
      ts = pt?.timestamp ?? new Date().toISOString()
    } catch {
      ts = new Date().toISOString()
    }
    const name =
      'Bookmark · ' +
      new Date(ts).toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    await createAnnotation({ name, start_time: ts })
    await this.reload()
  }
}

export const annotations = new AnnotationsStore()
