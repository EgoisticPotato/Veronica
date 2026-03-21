import { useState, useRef, useCallback } from 'react';

/**
 * useMediaRecorder
 *
 * Key fix: do NOT use timesliced recording (start(100ms)).
 * Chrome's MediaRecorder only writes a valid EBML/webm container header
 * in the FIRST dataavailable event. Timeslicing splits the stream into
 * fragments that, when concatenated, produce an invalid file ffmpeg
 * cannot parse ("EBML header parsing failed").
 *
 * Solution: start() with no timeslice, then call requestData() once
 * just before stop(). This yields a single, complete, valid blob.
 */
export function useMediaRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [error,       setError]       = useState(null);

  const recorderRef = useRef(null);
  const streamRef   = useRef(null);
  const blobRef     = useRef(null);  // holds the single final blob

  // Pick the best supported MIME type.
  // Prefer ogg/opus on Firefox, webm/opus on Chrome.
  const getSupportedMimeType = () => {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/ogg',
      'audio/mp4',
    ];
    for (const t of candidates) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return '';
  };

  const startRecording = useCallback(async () => {
    setError(null);
    blobRef.current = null;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount:    1,
          sampleRate:      16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl:  true,
        },
      });
      streamRef.current = stream;

      const mimeType = getSupportedMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      recorderRef.current = recorder;

      const chunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data?.size > 0) chunks.push(e.data);
      };

      recorder.onstop = () => {
        // Build one complete blob from all chunks
        const type = recorder.mimeType || 'audio/webm';
        blobRef.current = new Blob(chunks, { type });

        // Release mic
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      };

      // NO timeslice argument — records as one continuous stream
      // so the EBML header is always present at the start of the data
      recorder.start();
      setIsRecording(true);
    } catch (err) {
      setError(err.message);
      setIsRecording(false);
    }
  }, []);

  const stopRecording = useCallback(() => {
    return new Promise((resolve) => {
      const recorder = recorderRef.current;
      if (!recorder || recorder.state === 'inactive') {
        resolve(null);
        return;
      }

      // Override onstop to also resolve the promise
      const originalOnStop = recorder.onstop;
      recorder.onstop = (e) => {
        originalOnStop?.(e);
        setIsRecording(false);
        resolve(blobRef.current);
      };

      // requestData() flushes whatever has been recorded into ondataavailable
      // before stop() fires — ensures we get all audio
      recorder.requestData();
      recorder.stop();
    });
  }, []);

  return { isRecording, error, startRecording, stopRecording };
}
