import React, { useRef, useState, useCallback, useEffect } from 'react';
import { useParticleEngine } from '../hooks/useParticleEngine';
import { useMediaRecorder } from '../hooks/useMediaRecorder';
import { useSpotifyPlayer } from '../hooks/useSpotifyPlayer';
import { voiceService } from '../services/voiceService';
import SpotifyPlayer from './SpotifyPlayer';
import DocumentPanel from './DocumentPanel';
import './VeronicaUI.css';

// Quick frontend music-intent check — mirrors nlp_service._detect_music_intent
// Used to decide volume/pause behaviour BEFORE the API call returns
function isMusicOnlyIntent(text) {
  const q = text.toLowerCase().trim().replace(/\.$/, '');
  const STOP = ["stop the music", "stop music", "stop playing", "stop song", "mute", "silence", "turn off music", "stop it", "stop now"];
  const PAUSE = ["pause the music", "pause music", "pause song", "pause it", "can you pause", "please pause"];
  const RESUME = ["resume music", "continue music", "continue playing", "unpause", "play again", "keep playing", "play on", "resume playing"];
  const NEXT = ["next song", "next track", "skip this", "skip song", "next one", "play next", "change song", "change track"];
  const PREV = ["previous song", "previous track", "go back", "last song", "play previous", "back to"];
  const QUEUE = ["to the queue", "to queue", "to my queue", "in the queue", "in queue", "add to queue", "queue up", "queue this"];
  const EXACT = new Set(["stop", "pause", "resume", "next", "skip", "previous", "prev", "back", "silence", "unpause", "forward"]);
  if (EXACT.has(q)) return true;
  for (const kw of [...STOP, ...PAUSE, ...RESUME, ...NEXT, ...PREV, ...QUEUE])
    if (q.includes(kw)) return true;
  if (q.startsWith("queue ")) return true;
  if (q.startsWith("add ") && !q.includes("to play") && !q.includes("to playlist")) return true;
  if (q.startsWith("play ") || q.startsWith("listen to ")) return true;
  return false;
}

function VeronicaUI({ token }) {
  const canvasRef = useRef(null);

  const [uiState, setUiState] = useState('idle');
  const [statusText, setStatusText] = useState('tap to speak');
  const [transcript, setTranscript] = useState('');
  const [response, setResponse] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [authError, setAuthError] = useState(null);
  const [textInput, setTextInput] = useState('');
  const [activeDocIds, setActiveDocIds] = useState(new Set()); // active RAG documents (multiple)
  const [docPanelOpen, setDocPanelOpen] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  const [memories, setMemories] = useState([]);

  const stopSpeakingRef = useRef(null);
  const wasPlayingRef = useRef(false); // track if music was playing before hold-to-speak
  const originalVolumeRef = useRef(0.5);   // volume before ducking — restored after response
  const volumeRef = useRef(0.5);   // always-current mirror of volume state (matches useSpotifyPlayer initial)
  const uiStateRef = useRef(uiState);
  const inputRef = useRef(null);

  useEffect(() => { uiStateRef.current = uiState; }, [uiState]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get('auth_error');
    if (err) { setAuthError(err); window.history.replaceState({}, '', '/'); }
  }, []);

  const {
    deviceId, isReady: spotifyReady, isActive: spotifyActive,
    isPaused, currentTrack, position, duration, volume,
    error: spotifyError, queue, refreshQueue, handleLocalQueueReorder,
    togglePlay, nextTrack, previousTrack, seek, setVolume, activateElement, playUri,
  } = useSpotifyPlayer(token);

  useEffect(() => { volumeRef.current = volume; }, [volume]); // mirror — always current, no stale closure
  const { pushFrequencyData } = useParticleEngine(canvasRef, uiState);
  const { isRecording, error: micError, startRecording, stopRecording } = useMediaRecorder();


  // ── Helpers ───────────────────────────────────────────────────────────────

  const resetToIdle = useCallback((delay = 0) => {
    const fn = () => { setUiState('idle'); setStatusText('tap to speak'); };
    delay ? setTimeout(fn, delay) : fn();
  }, []);

  const interruptSpeaking = useCallback(() => {
    if (stopSpeakingRef.current) {
      stopSpeakingRef.current();
      stopSpeakingRef.current = null;
    }
  }, []);

  const speakText = useCallback(async (text) => {
    setUiState('speaking');
    setStatusText('responding...');
    const onDone = () => {
      stopSpeakingRef.current = null;
      resetToIdle();
      // After spoken answer: resume music and restore original volume (not hardcoded 0.8)
      if (wasPlayingRef.current) {
        wasPlayingRef.current = false;
        voiceService.controlMusic('resume')
          .then(() => setVolume(originalVolumeRef.current))
          .catch(() => { });
      }
    };
    try {
      const blob = await voiceService.synthesize(text);
      const stopFn = await voiceService.playAudioWithAnalyzer(
        blob,
        (fd) => pushFrequencyData(fd),
        onDone,
      );
      stopSpeakingRef.current = stopFn;
    } catch (err) {
      console.error('TTS:', err);
      stopSpeakingRef.current = null;
      onDone();
    }
  }, [pushFrequencyData, resetToIdle, setVolume]);

  // ── Core query handler (shared by voice + text input) ─────────────────────

  const handleQuery = useCallback(async (text) => {
    if (!text.trim()) return;

    setIsProcessing(true);
    setStatusText('processing...');
    setTranscript(text);
    setResponse('');

    // Activate Spotify element — required for play to work from text input too
    activateElement?.();
    // Give SDK 800ms to register device before play API call
    await new Promise((r) => setTimeout(r, 800));

    try {
      const nlpResult = await voiceService.query(text, Array.from(activeDocIds));
      setResponse(nlpResult.response);

      if (nlpResult.is_music) {
        const action = nlpResult.music_action;

        if (action === 'play' && nlpResult.music_query) {
          // Restore volume before playing — then speak the confirmation
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => { }); }
          if (!deviceId) {
            const msg = 'Spotify is not connected. Select Veronica in Spotify first.';
            setResponse(msg);
            await speakText(msg);
          } else {
            try {
              const r = await voiceService.playOnSpotify(nlpResult.music_query, deviceId);
              const msg = `Playing ${r.track_name} by ${r.artist_name}.`;
              setResponse(msg);
              await speakText(msg);
            } catch (e) { console.error('Play error:', e); }
          }

        } else if (action === 'stop' || action === 'pause') {
          // Pause: stop music fully, no TTS, clear volume duck
          wasPlayingRef.current = false;
          try { await voiceService.controlMusic('pause'); } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'resume') {
          wasPlayingRef.current = false;
          try {
            await setVolume(originalVolumeRef.current);
            await voiceService.controlMusic('resume');
          } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'next') {
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => { }); }
          try { await voiceService.controlMusic('next'); } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'previous') {
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => { }); }
          try { await voiceService.controlMusic('previous'); } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'queue' && nlpResult.music_query) {
          // Restore volume immediately — no TTS, music flows uninterrupted
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => { }); }
          try {
            const r = await voiceService.addToQueue(nlpResult.music_query, deviceId);
            const msg = `Added ${r.track_name} by ${r.artist_name} to your queue.`;
            setResponse(msg);   // shows in UI only — no speakText
            refreshQueue();
          } catch (e) {
            console.error('Queue error:', e);
            setResponse('Could not add that to the queue.');
          }

        } else {
          await speakText(nlpResult.response);
        }
        return;
      }

      // General query — was ducked during recording, now pause fully for spoken answer
      if (wasPlayingRef.current) {
        await voiceService.controlMusic('pause').catch(() => { });
        // wasPlayingRef stays true — speakText's onEnd will resume at full volume
      }
      await speakText(nlpResult.response);
    } catch (err) {
      console.error('Pipeline:', err);
      setStatusText('error — try again');
      resetToIdle(2500);
      if (wasPlayingRef.current) {
        wasPlayingRef.current = false;
        voiceService.controlMusic('resume').catch(() => { });
      }
    } finally {
      setIsProcessing(false);
    }
  }, [deviceId, activateElement, speakText, resetToIdle, activeDocIds]);

  // ── Voice input ───────────────────────────────────────────────────────────

  const handleMicPress = useCallback(async () => {
    if (isProcessing || isRecording) return;
    if (uiStateRef.current === 'speaking') {
      interruptSpeaking();
      await new Promise((r) => setTimeout(r, 80));
    }
    activateElement?.();
    // Duck volume to 5% — only if not already ducked (Space hold does this first)
    if (!isPaused && !wasPlayingRef.current) {
      wasPlayingRef.current = true;
      originalVolumeRef.current = volumeRef.current;
      setVolume(0.05).catch(() => { });
    }
    setUiState('listening');
    setStatusText('listening...');
    setTranscript('');
    setResponse('');
    await startRecording();
  }, [isProcessing, isRecording, isPaused, startRecording, activateElement, interruptSpeaking, setVolume]);



  const handleMicRelease = useCallback(async () => {
    if (!isRecording) return;
    setIsProcessing(true);
    setStatusText('processing...');

    const audioBlob = await stopRecording();
    if (!audioBlob || audioBlob.size < 500) { setIsProcessing(false); resetToIdle(); return; }

    try {
      const text = await voiceService.transcribe(audioBlob);
      if (!text.trim()) {
        setStatusText('could not understand');
        setIsProcessing(false);
        resetToIdle(1800);
        if (wasPlayingRef.current) {
          wasPlayingRef.current = false;
          voiceService.controlMusic('resume').catch(() => { });
        }
        return;
      }
      await handleQuery(text);
    } catch (err) {
      console.error('Transcription error:', err);
      setStatusText('error — try again');
      resetToIdle(2500);
    } finally {
      setIsProcessing(false);
    }
  }, [isRecording, stopRecording, handleQuery, resetToIdle]);

  // ── Text input submit ─────────────────────────────────────────────────────

  const handleTextSubmit = useCallback(async () => {
    const text = textInput.trim();
    if (!text || isProcessing) return;
    if (uiStateRef.current === 'speaking') interruptSpeaking();
    // Music commands: never pause — action handled inline with no TTS interruption
    // General queries: pause music, speak answer, then resume
    const musicOnly = isMusicOnlyIntent(text);
    if (!musicOnly && !isPaused) {
      wasPlayingRef.current = true;
      voiceService.controlMusic('pause').catch(() => { });
    }
    // For music-only intents, don't touch wasPlayingRef — music keeps playing
    setTextInput('');
    setIsProcessing(true);
    setStatusText('processing...');
    setTranscript(text);
    setResponse('');
    inputRef.current?.blur();
    await handleQuery(text);
  }, [textInput, isProcessing, isPaused, handleQuery, interruptSpeaking]);

  // ── Screenshot analysis ─────────────────────────────────────────────────────

  const handleScreenshot = useCallback(async () => {
    if (isProcessing) return;
    setIsProcessing(true);
    setStatusText('capturing screen...');
    try {
      const question = textInput.trim() || 'What do you see in this screenshot?';
      setTextInput('');
      const description = await voiceService.analyseScreenshot(question);
      setTranscript(question);
      setResponse(description);
      await speakText(description);
    } catch (e) {
      const msg = e.message || 'Screen capture failed.';
      setStatusText(msg);
      resetToIdle(2500);
    } finally {
      setIsProcessing(false);
    }
  }, [isProcessing, textInput, speakText, resetToIdle]);

  // ── Memory ────────────────────────────────────────────────────────────────

  const handleLoadMemory = useCallback(async () => {
    const data = await voiceService.getMemory();
    setMemories(data.memories || []);
    setShowMemory(true);
  }, []);

  const handleClearMemory = useCallback(async () => {
    await voiceService.clearMemory();
    setMemories([]);
  }, []);

  // ── Space bar ────────────────────────────────────────────────────────────
  // Behaviour:
  //   Speaking state:  any press → interrupt TTS + start recording
  //   Idle + music:    short tap (<300ms) → pause/resume music
  //                    hold (≥300ms)      → start recording
  //   Idle, no music:  hold → start recording

  const spaceDownTimeRef = useRef(null);
  const spaceHoldFiredRef = useRef(false);

  useEffect(() => {
    const dn = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (e.code !== 'Space' || e.repeat) return;
      e.preventDefault();

      const s = uiStateRef.current;

      if (s === 'speaking') {
        // Always barge-in regardless of hold duration
        handleMicPress();
        return;
      }

      if (!isRecording && !isProcessing) {
        spaceDownTimeRef.current = Date.now();
        spaceHoldFiredRef.current = false;

        // After 300ms, treat as "hold to speak"
        spaceDownTimeRef._timer = setTimeout(() => {
          spaceHoldFiredRef.current = true;
          // Duck to 5% instead of pausing — lets user speak clearly
          if (!isPaused) {
            wasPlayingRef.current = true;
            originalVolumeRef.current = volumeRef.current;
            setVolume(0.05).catch(() => { });
          }
          handleMicPress();
        }, 300);
      }
    };

    const up = (e) => {
      const tag2 = document.activeElement?.tagName;
      if (tag2 === 'INPUT' || tag2 === 'TEXTAREA') return;
      if (e.code !== 'Space') return;
      e.preventDefault();

      clearTimeout(spaceDownTimeRef._timer);

      if (isRecording) {
        // Was holding — release mic
        handleMicRelease();
        return;
      }

      const held = spaceHoldFiredRef.current;
      const downTime = spaceDownTimeRef.current;
      const duration = downTime ? Date.now() - downTime : 999;
      spaceDownTimeRef.current = null;
      spaceHoldFiredRef.current = false;

      if (!held && duration < 300 && !isProcessing) {
        // Short tap while idle — toggle music pause/resume
        const s = uiStateRef.current;
        if (s === 'idle') {
          voiceService.controlMusic(isPaused ? 'resume' : 'pause').catch(() => { });
        }
      }
    };

    window.addEventListener('keydown', dn);
    window.addEventListener('keyup', up);
    return () => {
      clearTimeout(spaceDownTimeRef._timer);
      window.removeEventListener('keydown', dn);
      window.removeEventListener('keyup', up);
    };
  }, [handleMicPress, handleMicRelease, isRecording, isProcessing, isPaused]);

  // Orb state class
  const orbClass = isRecording ? 'listening' : isProcessing ? 'processing' : uiState === 'speaking' ? 'speaking' : '';

  // Status bar state class
  const statusBarState = isRecording ? 'listening' : isProcessing ? 'processing' : uiState === 'speaking' ? 'speaking' : 'idle';
  const statusBarVisible = statusBarState !== 'idle';

  return (
    <>
      {/* Neural Canvas */}
      <canvas ref={canvasRef} className="neural-canvas" />
      <div className="vignette" />

      {/* Toast */}
      <div className={`v-toast ${(spotifyError || authError) ? 'show' : ''}`}>
        {authError ? `Login failed: ${authError}` : spotifyError}
      </div>

      {/* Header */}
      <div className="v-header">
        <div className="v-logo">
          <div className="v-logo-dot" />
          veronica
        </div>
        <div className="v-header-actions">
          <button
            className={`pill-btn ${docPanelOpen ? 'active' : ''}`}
            onClick={() => setDocPanelOpen(!docPanelOpen)}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            Docs{activeDocIds.size > 0 ? ` (${activeDocIds.size})` : ''}
          </button>
          <button
            className={`pill-btn ${showMemory ? 'active' : ''}`}
            onClick={showMemory ? () => setShowMemory(false) : handleLoadMemory}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
            Memory
          </button>
          <button
            className={`pill-btn ${spotifyActive ? 'active' : ''}`}
            onClick={() => {/* Spotify auto-shows when active */}}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.65 14.4c-.22.35-.66.46-1.01.25-2.76-1.68-6.24-2.06-10.34-1.13-.39.09-.78-.15-.87-.54-.09-.39.15-.78.54-.87 4.48-1.02 8.33-.58 11.43 1.28.34.22.46.66.25 1.01zm1.24-2.76c-.27.43-.84.56-1.27.3-3.16-1.94-7.97-2.5-11.7-1.37-.48.14-.98-.14-1.12-.62-.14-.48.14-.98.62-1.12 4.26-1.29 9.55-.67 13.17 1.55.43.27.56.84.3 1.26zm.11-2.87c-3.79-2.25-10.04-2.46-13.66-1.36-.58.18-1.19-.15-1.37-.73-.18-.58.15-1.19.73-1.37 4.15-1.26 11.05-1.02 15.41 1.57.52.31.7 1 .39 1.52-.31.52-1 .7-1.5.37z"/></svg>
            Music
          </button>
        </div>
      </div>

      {/* Status Bar */}
      <div className={`v-status-bar ${statusBarVisible ? 'visible' : ''} state-${statusBarState}`}>
        <div className="v-status-dot" />
        <span>{statusText}</span>
      </div>

      {/* Main App */}
      <div className="veronica-app">
        <div className="v-center-core">

          {/* Orb System */}
          <div className="orb-system">
            <div className="orb-ring orb-ring-1" />
            <div className="orb-ring orb-ring-2" />
            <div className="orb-ring orb-ring-3" />
            <div className="pulse-ring" />
            <div className="pulse-ring" />
            <div className="pulse-ring" />

            <button
              className={`orb-btn ${orbClass}`}
              onMouseDown={handleMicPress}
              onMouseUp={handleMicRelease}
              onTouchStart={(e) => { e.preventDefault(); handleMicPress(); }}
              onTouchEnd={(e) => { e.preventDefault(); handleMicRelease(); }}
              disabled={isProcessing}
              title="Hold SPACE or click to talk"
            >
              <svg className="orb-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
              </svg>
            </button>

            {/* Waveform */}
            <div className={`waveform ${isRecording ? 'visible' : ''}`}>
              <div className="wave-bar" /><div className="wave-bar" /><div className="wave-bar" />
              <div className="wave-bar" /><div className="wave-bar" /><div className="wave-bar" />
              <div className="wave-bar" />
            </div>
          </div>

          {/* Conversation */}
          <div className="conversation-area">
            {transcript && (
              <div className="msg user">
                <div className="msg-avatar">YOU</div>
                <div className="msg-bubble">{transcript}</div>
              </div>
            )}
            {isProcessing && !response && (
              <div className="msg veronica">
                <div className="msg-avatar">V</div>
                <div className="msg-bubble">
                  <div className="typing-indicator">
                    <div className="typing-dot" /><div className="typing-dot" /><div className="typing-dot" />
                  </div>
                </div>
              </div>
            )}
            {response && (
              <div className="msg veronica">
                <div className="msg-avatar">V</div>
                <div className="msg-bubble">{response}</div>
              </div>
            )}
          </div>

          {/* Input Row */}
          <div className="input-row">
            <input
              ref={inputRef}
              type="text"
              className="text-input"
              placeholder="Type a message or hold SPACE to speak..."
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleTextSubmit(); }}
              disabled={isProcessing || isRecording}
              autoComplete="off"
              spellCheck="false"
            />
            <button
              className="screenshot-btn"
              onClick={handleScreenshot}
              disabled={isProcessing}
              title="Analyse screenshot"
            >
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z" />
              </svg>
            </button>
            <button
              className="send-btn"
              onClick={handleTextSubmit}
              disabled={!textInput.trim() || isProcessing || isRecording}
              aria-label="Send"
            >
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
              </svg>
            </button>
          </div>

          <div className="hold-hint">Hold <kbd>SPACE</kbd> to speak — release to send</div>

        </div>
      </div>

      {/* Memory Panel (floating bottom-left) */}
      <div className={`memory-panel ${showMemory ? 'visible' : ''}`}>
        <div className="memory-title">
          <span>Remembered</span>
          <button className="memory-clear-btn" onClick={handleClearMemory}>clear</button>
        </div>
        <div>
          {memories.length === 0
            ? <div className="memory-item"><span style={{color:'var(--text-dim)',fontSize:'11px'}}>Nothing yet — start talking!</span></div>
            : memories.slice(-8).map((m, i) => (
              <div key={i} className="memory-item">
                <span className="memory-key">{m.category}:</span>
                <span>{m.fact}</span>
              </div>
            ))
          }
        </div>
      </div>

      {/* Spotify Panel (floating bottom-right) */}
      {spotifyActive && (
        <SpotifyPlayer
          currentTrack={currentTrack}
          isPaused={isPaused}
          isActive={spotifyActive}
          position={position}
          duration={duration}
          onTogglePlay={togglePlay}
          onNext={nextTrack}
          onPrev={previousTrack}
          onSeek={seek}
          onVolumeChange={setVolume}
          volume={volume}
          queue={queue}
          deviceId={deviceId}
          onQueueChange={refreshQueue}
          onPlayUri={playUri}
          onLocalQueueReorder={handleLocalQueueReorder}
        />
      )}

      {micError && <div className="mic-error">Mic: {micError}</div>}

      <DocumentPanel
        isOpen={docPanelOpen}
        onClose={() => setDocPanelOpen(false)}
        activeDocIds={activeDocIds}
        onDocSelect={(docId) => {
          setActiveDocIds((prev) => {
            const next = new Set(prev);
            if (!docId) {
              voiceService.clearHistory();
              return new Set();
            }
            if (next.has(docId)) {
              next.delete(docId);
            } else {
              next.add(docId);
            }
            if (next.size === 0) voiceService.clearHistory();
            return next;
          });
        }}
      />
    </>
  );
}

export default VeronicaUI;