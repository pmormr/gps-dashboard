// CodeMirror wrapper for the Docs edit mode. Heavy (~350 kB min), so this module
// is dynamic-imported only when the user actually hits Edit — it never touches
// the main bundle (same pattern as mermaid in docs.ts).

import { markdown } from '@codemirror/lang-markdown'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView, basicSetup } from 'codemirror'

/** A mounted markdown editor over one vault file. */
export interface DocEditor {
  /** The current draft text. */
  getValue(): string
  /** Whether the draft differs from the text the editor was opened with. */
  isDirty(): boolean
  /** Tear down the editor and its DOM. */
  destroy(): void
  focus(): void
}

/**
 * Mount a markdown CodeMirror editor into `parent`.
 *
 * @param parent - The container element (emptied on destroy).
 * @param initial - The file content to edit.
 */
export function createEditor(parent: HTMLElement, initial: string): DocEditor {
  const view = new EditorView({
    doc: initial,
    parent,
    extensions: [basicSetup, markdown(), oneDark, EditorView.lineWrapping],
  })
  return {
    getValue: () => view.state.doc.toString(),
    isDirty: () => view.state.doc.toString() !== initial,
    destroy: () => view.destroy(),
    focus: () => view.focus(),
  }
}
