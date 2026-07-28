import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, getToken, onTokenCleared, setToken } from '../token'
import { TokenGate } from './TokenGate'

describe('TokenGate', () => {
  beforeEach(() => {
    clearToken()
  })

  it('asks for the token and stores what is entered', async () => {
    const onUnlocked = vi.fn()
    const user = userEvent.setup()
    render(<TokenGate onUnlocked={onUnlocked} />)

    expect(screen.getByText(/access-restricted/i)).toBeInTheDocument()
    await user.type(screen.getByLabelText(/access token/i), 'demo-token')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(getToken()).toBe('demo-token')
    expect(onUnlocked).toHaveBeenCalled()
  })

  it('does not echo the token on screen', async () => {
    const user = userEvent.setup()
    render(<TokenGate onUnlocked={vi.fn()} />)

    const field = screen.getByLabelText(/access token/i)
    await user.type(field, 'secret')

    expect(field).toHaveAttribute('type', 'password')
  })

  it('will not submit an empty or whitespace-only token', async () => {
    const onUnlocked = vi.fn()
    const user = userEvent.setup()
    render(<TokenGate onUnlocked={onUnlocked} />)

    expect(screen.getByRole('button', { name: /continue/i })).toBeDisabled()
    await user.type(screen.getByLabelText(/access token/i), '   ')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(onUnlocked).not.toHaveBeenCalled()
    expect(getToken()).toBeNull()
  })

  it('says so when a token was refused rather than just re-asking', async () => {
    render(<TokenGate rejected onUnlocked={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/was not accepted/i)
  })

  it('shows no rejection notice on first load', () => {
    render(<TokenGate onUnlocked={vi.fn()} />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('token store', () => {
  beforeEach(() => {
    clearToken()
  })

  it('keeps the token for the tab, not beyond it', () => {
    setToken('abc')

    // sessionStorage, not localStorage: it dies with the tab.
    expect(sessionStorage.getItem('trust7.demoToken')).toBe('abc')
    expect(localStorage.getItem('trust7.demoToken')).toBeNull()
  })

  it('trims what is entered so a pasted trailing space does not 401', () => {
    setToken('  abc  ')

    expect(getToken()).toBe('abc')
  })

  it('notifies subscribers when the token is dropped', () => {
    const listener = vi.fn()
    const unsubscribe = onTokenCleared(listener)
    setToken('abc')

    clearToken()
    expect(listener).toHaveBeenCalledTimes(1)

    unsubscribe()
    clearToken()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
