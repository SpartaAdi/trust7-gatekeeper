/**
 * A reviewer's own OpenRouter key, held in memory for this page load only.
 *
 * Deliberately NOT `sessionStorage` — which is what `token.ts` uses for the demo
 * token, and the difference is the point. The demo token is a shared, rotatable
 * gate; this is a billable credential belonging to one person. Browsers write
 * session storage to disk to support session restore, so persisting it there
 * would put a live API key in a file on the machine, outliving the tab that
 * created it.
 *
 * The cost of that choice is real and is stated in the UI: a refresh clears the
 * key and it has to be entered again. That is the intended trade.
 *
 * There is no listener/subscription machinery here on purpose. Nothing else in
 * the app needs to react when the key changes — only `submitReview` reads it.
 */

let cached = ''

export function getApiKey(): string {
  return cached
}

export function setApiKey(key: string): void {
  cached = key.trim()
}

export function clearApiKey(): void {
  cached = ''
}

/** Enough of the key to recognise it, never enough to use it. */
export function maskApiKey(key: string): string {
  const trimmed = key.trim()
  if (trimmed.length <= 10) return '•'.repeat(trimmed.length)
  return `${trimmed.slice(0, 6)}…${trimmed.slice(-4)}`
}
