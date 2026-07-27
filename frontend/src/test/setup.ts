import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// Any test that reaches the network is a bug in the test, not a slow test —
// fail loudly rather than hanging or silently hitting a real host.
vi.stubGlobal(
  'fetch',
  vi.fn(() => {
    throw new Error('Unmocked fetch call — mock the api module in this test.')
  }),
)
