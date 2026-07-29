import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearApiKey, getApiKey, maskApiKey, setApiKey } from './apiKey'

const KEY = 'sk-or-v1-user-supplied-key-a1b2c3d4e5f6a7b8c9d0'

describe('apiKey', () => {
  afterEach(() => {
    clearApiKey()
    vi.restoreAllMocks()
  })

  it('starts empty, so no key means the server key', () => {
    expect(getApiKey()).toBe('')
  })

  it('round-trips a key and trims it', () => {
    setApiKey(`  ${KEY}\n`)

    expect(getApiKey()).toBe(KEY)
  })

  it('clears on request', () => {
    setApiKey(KEY)
    clearApiKey()

    expect(getApiKey()).toBe('')
  })

  /**
   * The whole reason this module exists separately from `token.ts`. A live
   * billable credential in session storage is written to disk by browsers that
   * support session restore, and outlives the tab that created it.
   */
  it('never touches browser storage', () => {
    const sessionSet = vi.spyOn(Storage.prototype, 'setItem')

    setApiKey(KEY)

    expect(sessionSet).not.toHaveBeenCalled()
    expect(sessionStorage.length).toBe(0)
    expect(localStorage.length).toBe(0)
  })

  it('is gone after a reload, because nothing outside memory holds it', () => {
    setApiKey(KEY)

    // A reload is a fresh module instance; the closest a unit test gets is
    // asserting that no persisted copy exists for one to read back.
    expect(
      Object.keys(sessionStorage).concat(Object.keys(localStorage)),
    ).toHaveLength(0)
  })

  describe('maskApiKey', () => {
    it('shows enough to recognise and not enough to use', () => {
      const masked = maskApiKey(KEY)

      expect(masked).toBe('sk-or-…c9d0')
      expect(masked).not.toContain('user-supplied')
      expect(masked.length).toBeLessThan(KEY.length)
    })

    it('reveals nothing at all for a short string', () => {
      expect(maskApiKey('sk-or-abc')).toBe('•••••••••')
    })

    it('handles empty input', () => {
      expect(maskApiKey('')).toBe('')
    })
  })
})
