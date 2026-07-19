import { onDestroy } from 'svelte'

/** Reactive handle returned by {@link useToast}; render it with `<Toast c={…} />`. */
export interface ToastController {
  readonly show: boolean
  readonly msg: string
  readonly err: boolean
  /** Flash a message; `err` tints it as a failure. Re-arms the auto-dismiss timer. */
  toast(msg: string, err?: boolean): void
}

/**
 * Transient bottom-of-screen toast state: `toast(msg, err?)` shows a message
 * that auto-dismisses after `timeoutMs`. The dismiss timer is cleared on
 * unmount, so call this at component init.
 *
 * Args:
 *   timeoutMs: How long a message stays visible before fading.
 */
export function useToast(timeoutMs: number): ToastController {
  let msg = $state('')
  let err = $state(false)
  let show = $state(false)
  let timer: number | undefined

  onDestroy(() => {
    if (timer) clearTimeout(timer)
  })

  return {
    get show() {
      return show
    },
    get msg() {
      return msg
    },
    get err() {
      return err
    },
    toast(m: string, e = false): void {
      msg = m
      err = e
      show = true
      clearTimeout(timer)
      timer = window.setTimeout(() => (show = false), timeoutMs)
    },
  }
}
