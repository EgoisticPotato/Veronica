/**
 * useWakeWord
 *
 * Listens for "veronica" using the Web Speech Recognition API.
 *
 * Flow when wake word detected:
 *   1. Stop wake word recognition
 *   2. Call onWakeWord() → opens mic (handleMicPress)
 *   3. Wait LISTEN_DURATION ms → auto-release mic (handleMicRelease)
 *   4. After 3s → restart wake word listener
 *
 * This auto-release is what was missing — without it the mic stayed open forever.
 */

import { useEffect, useRef, useCallback, useState } from 'react';

const WAKE_WORD      = 'veronica';
const LISTEN_DURATION = 5000; // ms to record after wake word before auto-releasing

export function useWakeWord({
  onWakeWord,
  onRelease,        // called after LISTEN_DURATION to stop recording
  enabled = true,
  suppressed = false,
}) {
  const recognitionRef  = useRef(null);
  const enabledRef      = useRef(enabled);
  const suppressedRef   = useRef(suppressed);
  const onWakeWordRef   = useRef(onWakeWord);
  const onReleaseRef    = useRef(onRelease);
  const restartTimerRef = useRef(null);
  const listenTimerRef  = useRef(null);
  const startedRef      = useRef(false);

  const [isListening, setIsListening] = useState(false);
  const [supported,   setSupported]   = useState(false);

  useEffect(() => { enabledRef.current    = enabled;    }, [enabled]);
  useEffect(() => { suppressedRef.current = suppressed; }, [suppressed]);
  useEffect(() => { onWakeWordRef.current = onWakeWord; }, [onWakeWord]);
  useEffect(() => { onReleaseRef.current  = onRelease;  }, [onRelease]);

  const stopRecognition = useCallback(() => {
    clearTimeout(restartTimerRef.current);
    clearTimeout(listenTimerRef.current);
    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (_) {}
      recognitionRef.current = null;
    }
    startedRef.current = false;
    setIsListening(false);
  }, []);

  const startRecognition = useCallback(() => {
    if (!enabledRef.current) return;
    if (startedRef.current) return;

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;

    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (_) {}
    }

    const rec = new SR();
    rec.lang            = 'en-US';
    rec.continuous      = true;
    rec.interimResults  = true;
    rec.maxAlternatives = 3;

    rec.onstart = () => {
      startedRef.current = true;
      setIsListening(true);
    };

    rec.onresult = (event) => {
      if (suppressedRef.current) return;

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        for (let j = 0; j < result.length; j++) {
          const t = result[j].transcript.toLowerCase().trim();
          if (t.includes(WAKE_WORD)) {
            console.log('[WakeWord] Triggered by:', t);

            // 1. Stop wake word listener so mic is free
            stopRecognition();

            // 2. Open mic
            onWakeWordRef.current?.();

            // 3. Auto-release after LISTEN_DURATION
            listenTimerRef.current = setTimeout(() => {
              onReleaseRef.current?.();

              // 4. Restart wake listener after conversation settles
              restartTimerRef.current = setTimeout(startRecognition, 3000);
            }, LISTEN_DURATION);

            return;
          }
        }
      }
    };

    rec.onend = () => {
      startedRef.current = false;
      setIsListening(false);
      if (enabledRef.current) {
        restartTimerRef.current = setTimeout(startRecognition, 300);
      }
    };

    rec.onerror = (e) => {
      startedRef.current = false;
      setIsListening(false);
      if (e.error !== 'no-speech' && e.error !== 'aborted') {
        console.warn('[WakeWord] error:', e.error);
      }
      if (enabledRef.current && e.error !== 'not-allowed') {
        restartTimerRef.current = setTimeout(startRecognition, 800);
      }
    };

    recognitionRef.current = rec;
    try {
      rec.start();
    } catch (e) {
      startedRef.current = false;
      console.warn('[WakeWord] start failed:', e.message);
    }
  }, [stopRecognition]);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    setSupported(!!SR);
  }, []);

  useEffect(() => {
    if (!supported) return;
    if (enabled) {
      startRecognition();
    } else {
      stopRecognition();
    }
    return () => {
      clearTimeout(restartTimerRef.current);
      clearTimeout(listenTimerRef.current);
    };
  }, [enabled, supported, startRecognition, stopRecognition]);

  return { isListening, supported };
}
