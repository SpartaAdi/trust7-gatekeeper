/**
 * The demo access token, held for the life of the browser tab.
 *
 * `sessionStorage`, not `localStorage`: the token dies with the tab, so a shared
 * or borrowed machine does not keep it around after the demo. A module-level
 * cache backs it so a page with storage disabled (private mode in some browsers
 * throws on access) still works for the current page load.
 *
 * This is a one-day gate. There is no refresh, no expiry, and no user identity —
 * if any of that becomes necessary, this file should be deleted rather than grown.
 */

const KEY = 'trust7.demoToken'

let cached: string | null = null

export function getToken(): string | null {
  if (cached !== null) return cached
  try {
    cached = sessionStorage.getItem(KEY)
  } catch {
    // Storage unavailable; the in-memory cache is the whole mechanism.
    cached = null
  }
  return cached
}

export function setToken(token: string): void {
  cached = token.trim()
  try {
    sessionStorage.setItem(KEY, cached)
  } catch {
    // Not fatal: the token still works until the page is reloaded.
  }
}

export function clearToken(): void {
  cached = null
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // Nothing to do — the cache is already cleared.
  }
  for (const listener of listeners) listener()
}

const listeners = new Set<() => void>()

/**
 * Called when the token is dropped, which only happens on a 401.
 *
 * A subscription rather than polling: the API client is what discovers the token
 * is bad, and `App` is what has to react, and they do not otherwise know about
 * each other. Returns an unsubscribe function for `useEffect`.
 */
export function onTokenCleared(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
