/** Error-to-message helpers shared across views and stores. */

/** Extract a display string from an unknown thrown value (`catch (e: unknown)`). */
export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
