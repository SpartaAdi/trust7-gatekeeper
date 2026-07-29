/**
 * Speech-to-text via the Web Speech API. Browser-only — no request, no dependency.
 *
 * `SpeechRecognition` is not in TypeScript's DOM lib and is prefixed in Chromium, so
 * the shape is declared narrowly here rather than pulling in a types package for one
 * field. Only what is used is described.
 *
 * `supported` is resolved once at module load and re-read per hook call. A caller that
 * gets `false` must render nothing: a mic button that cannot listen is worse than no
 * mic button, because the user tries it and learns nothing about why it failed.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

interface SpeechRecognitionAlternative {
  transcript: string
}
interface SpeechRecognitionResult {
  readonly length: number
  isFinal: boolean
  [index: number]: SpeechRecognitionAlternative
}
interface SpeechRecognitionResultList {
  readonly length: number
  [index: number]: SpeechRecognitionResult
}
interface SpeechRecognitionEventLike {
  resultIndex: number
  results: SpeechRecognitionResultList
}
interface SpeechRecognitionLike {
  continuous: boolean
  interimResults: boolean
  lang: string
  start: () => void
  stop: () => void
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: (() => void) | null
  onend: (() => void) | null
}
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

function constructorFor(): SpeechRecognitionConstructor | null {
  const scope = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor
    webkitSpeechRecognition?: SpeechRecognitionConstructor
  }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

export function dictationSupported(): boolean {
  return constructorFor() !== null
}

interface Dictation {
  supported: boolean
  listening: boolean
  toggle: () => void
}

/**
 * `onTranscript` receives only text that has been finalised, appended by the caller.
 * Interim results are deliberately dropped: they rewrite themselves as the recogniser
 * changes its mind, and a field that visibly rewrites what someone just said while
 * they are still speaking is worse than one that lags.
 */
export function useDictation(onTranscript: (text: string) => void): Dictation {
  const [listening, setListening] = useState(false)
  const recognition = useRef<SpeechRecognitionLike | null>(null)

  // Held in a ref so a re-created callback does not need to rebuild the recogniser
  // mid-utterance.
  const onTranscriptRef = useRef(onTranscript)
  onTranscriptRef.current = onTranscript

  // Stop listening if the component goes away — the recogniser holds the microphone,
  // and an abandoned one keeps the browser's recording indicator lit.
  useEffect(
    () => () => {
      recognition.current?.stop()
      recognition.current = null
    },
    [],
  )

  const toggle = useCallback(() => {
    if (recognition.current) {
      recognition.current.stop()
      return
    }

    const Recognition = constructorFor()
    if (!Recognition) return

    const instance = new Recognition()
    instance.continuous = true
    instance.interimResults = false
    instance.lang = navigator.language || 'en-US'

    instance.onresult = (event) => {
      let finalised = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (result?.isFinal) finalised += result[0]?.transcript ?? ''
      }
      if (finalised) onTranscriptRef.current(finalised)
    }
    // Both paths clear the ref, so a permission denial cannot leave the button stuck
    // in a listening state with no recogniser behind it.
    const finish = () => {
      recognition.current = null
      setListening(false)
    }
    instance.onerror = finish
    instance.onend = finish

    recognition.current = instance
    instance.start()
    setListening(true)
  }, [])

  return { supported: dictationSupported(), listening, toggle }
}
