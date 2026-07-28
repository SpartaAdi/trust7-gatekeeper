import { useState } from 'react'

import { setToken } from '../token'

interface Props {
  /** Set when a previous request was rejected, rather than on first load. */
  rejected?: boolean
  onUnlocked: () => void
}

/**
 * Shared-token entry. A one-day demo gate, not sign-in.
 *
 * There is no client-side validation of the token: the server is the only thing
 * that knows whether it is right, and pretending otherwise would mean two places
 * to keep in agreement. Entering it simply stores it and re-renders; a wrong token
 * comes back as a 401 on the first real request, which drops it and brings this
 * screen back with `rejected` set.
 */
export function TokenGate({ rejected, onUnlocked }: Props) {
  const [value, setValue] = useState('')

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!value.trim()) return
    setToken(value)
    onUnlocked()
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-minfy-navy px-6">
      <form
        onSubmit={submit}
        className="animate-enter w-full max-w-sm bg-surface px-7 py-8"
      >
        <span
          aria-hidden="true"
          className="mb-6 block h-1 w-12 rounded-full bg-minfy-orange"
        />
        <h1 className="t-title">Trust7 Gatekeeper</h1>
        <p className="t-body mt-2 text-ink-muted">
          This demo is access-restricted. Enter the access token to continue.
        </p>

        {rejected && (
          <p
            role="alert"
            className="t-caption mt-5 border-l-2 border-sev-high bg-surface-sunken px-3 py-2.5 text-sev-high"
          >
            That token was not accepted. Check it and try again.
          </p>
        )}

        <label className="t-caption mt-6 block font-medium" htmlFor="demo-token">
          Access token
        </label>
        <input
          id="demo-token"
          type="password"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          autoFocus
          autoComplete="off"
          className="t-body mt-1.5 w-full border border-hairline bg-surface px-3 py-2.5 focus:border-minfy-orange focus:outline-none"
        />

        <button
          type="submit"
          disabled={!value.trim()}
          className="t-body mt-5 w-full bg-minfy-orange px-5 py-2.5 font-semibold text-white transition-colors duration-150 hover:bg-minfy-navy disabled:cursor-not-allowed disabled:opacity-50"
        >
          Continue
        </button>

        <p className="t-caption mt-5 text-ink-faint">
          The token is held for this browser tab only and is cleared when the tab
          closes.
        </p>
      </form>
    </div>
  )
}
