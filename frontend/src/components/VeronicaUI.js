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
  const STOP    = ["stop the music","stop music","stop playing","stop song","mute","silence","turn off music","stop it","stop now"];
  const PAUSE   = ["pause the music","pause music","pause song","pause it","can you pause","please pause"];
  const RESUME  = ["resume music","continue music","continue playing","unpause","play again","keep playing","play on","resume playing"];
  const NEXT    = ["next song","next track","skip this","skip song","next one","play next","change song","change track"];
  const PREV    = ["previous song","previous track","go back","last song","play previous","back to"];
  const QUEUE   = ["to the queue","to queue","to my queue","in the queue","in queue","add to queue","queue up","queue this"];
  const EXACT   = new Set(["stop","pause","resume","next","skip","previous","prev","back","silence","unpause","forward"]);
  if (EXACT.has(q)) return true;
  for (const kw of [...STOP,...PAUSE,...RESUME,...NEXT,...PREV,...QUEUE])
    if (q.includes(kw)) return true;
  if (q.startsWith("queue ")) return true;
  if (q.startsWith("add ") && !q.includes("to play") && !q.includes("to playlist")) return true;
  if (q.startsWith("play ") || q.startsWith("listen to ")) return true;
  return false;
}

function VeronicaUI({ token }) {
  const canvasRef = useRef(null);

  const [uiState,      setUiState]      = useState('idle');
  const [statusText,   setStatusText]   = useState('tap to speak');
  const [transcript,   setTranscript]   = useState('');
  const [response,     setResponse]     = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [authError,    setAuthError]    = useState(null);
  const [textInput,    setTextInput]    = useState('');
  const [activeDocIds, setActiveDocIds] = useState(new Set()); // active RAG documents (multiple)
  const [docPanelOpen,    setDocPanelOpen]    = useState(false);
  const [showMemory,      setShowMemory]      = useState(false);
  const [memories,        setMemories]        = useState([]);

  const stopSpeakingRef   = useRef(null);
  const wasPlayingRef     = useRef(false); // track if music was playing before hold-to-speak
  const originalVolumeRef = useRef(0.5);   // volume before ducking — restored after response
  const volumeRef         = useRef(0.5);   // always-current mirror of volume state (matches useSpotifyPlayer initial)
  const uiStateRef      = useRef(uiState);
  const inputRef        = useRef(null);

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
          .catch(() => {});
      }
    };
    try {
      const result = await voiceService.synthesize(text);

      // Browser TTS fallback (no backend audio)
      if (result && result._browserTTS) {
        const stopBrowser = voiceService.speakWithBrowser(result.text, onDone);
        stopSpeakingRef.current = stopBrowser;
        return;
      }

      const stopFn = await voiceService.playAudioWithAnalyzer(
        result,
        (fd) => pushFrequencyData(fd),
        onDone,
      );
      stopSpeakingRef.current = stopFn;
    } catch (err) {
      console.error('TTS:', err);
      stopSpeakingRef.current = null;
      // Last resort: try browser TTS before going fully silent
      try {
        const stopBrowser = voiceService.speakWithBrowser(text, onDone);
        stopSpeakingRef.current = stopBrowser;
      } catch (_) {
        onDone();
      }
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
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => {}); }
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
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => {}); }
          try { await voiceService.controlMusic('next'); } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'previous') {
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => {}); }
          try { await voiceService.controlMusic('previous'); } catch (e) { console.error(e); }
          setResponse(nlpResult.response);

        } else if (action === 'queue' && nlpResult.music_query) {
          // Restore volume immediately — no TTS, music flows uninterrupted
          if (wasPlayingRef.current) { wasPlayingRef.current = false; setVolume(originalVolumeRef.current).catch(() => {}); }
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
        await voiceService.controlMusic('pause').catch(() => {});
        // wasPlayingRef stays true — speakText's onEnd will resume at full volume
      }
      await speakText(nlpResult.response);
    } catch (err) {
      console.error('Pipeline:', err);
      setStatusText('error — try again');
      resetToIdle(2500);
      if (wasPlayingRef.current) {
        wasPlayingRef.current = false;
        voiceService.controlMusic('resume').catch(() => {});
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
      setVolume(0.05).catch(() => {});
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
          voiceService.controlMusic('resume').catch(() => {});
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
      voiceService.controlMusic('pause').catch(() => {});
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
            setVolume(0.05).catch(() => {});
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
          voiceService.controlMusic(isPaused ? 'resume' : 'pause').catch(() => {});
        }
      }
    };

    window.addEventListener('keydown', dn);
    window.addEventListener('keyup',   up);
    return () => {
      clearTimeout(spaceDownTimeRef._timer);
      window.removeEventListener('keydown', dn);
      window.removeEventListener('keyup', up);
    };
  }, [handleMicPress, handleMicRelease, isRecording, isProcessing, isPaused]);

  return (
    <div className={`veronica-root ${spotifyActive ? 'split-active' : ''}`}>

      <div className="veronica-left">
        <canvas ref={canvasRef} className="particle-canvas" />

        {/* Mic button */}
        <button
          className={`mic-btn ${isRecording ? 'mic-active' : ''} ${isProcessing ? 'mic-processing' : ''}`}
          onMouseDown={handleMicPress}
          onMouseUp={handleMicRelease}
          onTouchStart={(e) => { e.preventDefault(); handleMicPress(); }}
          onTouchEnd={(e)   => { e.preventDefault(); handleMicRelease(); }}
          disabled={isProcessing}
          aria-label="Hold to speak"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            {isProcessing
              ? <circle cx="12" cy="12" r="3" opacity="0.7" />
              : <path d="M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z" />
            }
          </svg>
        </button>

        {/* HUD */}
        <div className="veronica-hud">
          <div className="veronica-status">
            <span className={`state-dot state-${uiState}`} />
            <span className="status-text">{statusText}</span>
          </div>

          {(transcript || response) && (
            <div className="conversation">
              {transcript && (
                <div className="conv-row conv-user">
                  <span className="conv-label">you</span>
                  <span className="conv-text">{transcript}</span>
                </div>
              )}
              {response && (
                <div className="conv-row conv-veronica">
                  <span className="conv-label">veronica</span>
                  <span className="conv-text">{response}</span>
                </div>
              )}
            </div>
          )}

          {/* Text input */}
          <div className="text-input-row">
            <input
              ref={inputRef}
              type="text"
              className="text-input"
              placeholder="or type here..."
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleTextSubmit(); }}
              disabled={isProcessing || isRecording}
              autoComplete="off"
              spellCheck="false"
            />
            <button
              className="text-send-btn"
              onClick={handleTextSubmit}
              disabled={!textInput.trim() || isProcessing || isRecording}
              aria-label="Send"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
              </svg>
            </button>
          </div>

          <div className="keyboard-hint">
            {uiState === 'speaking' ? 'hold space to interrupt' : 'hold space to speak'}
          </div>

          {/* Document panel trigger */}
          {/* ── Toolbar ── */}
          <div className="veronica-toolbar">

            {/* Documents */}
            <button
              className={`toolbar-btn ${activeDocIds.size > 0 ? 'toolbar-btn-active' : ''}`}
              onClick={() => setDocPanelOpen(true)}
              title="Documents"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                <path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z"/>
              </svg>
              {activeDocIds.size > 0 ? `${activeDocIds.size} doc${activeDocIds.size > 1 ? 's' : ''} active` : 'docs'}
            </button>

            {/* Screenshot */}
            <button
              className="toolbar-btn"
              onClick={handleScreenshot}
              disabled={isProcessing}
              title="Analyse screenshot (or type a question first)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                <path d="M20 5h-3.17L15 3H9L7.17 5H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm-8 13c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/>
              </svg>
              screenshot
            </button>

            {/* Memory */}
            <button
              className={`toolbar-btn ${showMemory ? 'toolbar-btn-active' : ''}`}
              onClick={showMemory ? () => setShowMemory(false) : handleLoadMemory}
              title="View conversation memory"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
              </svg>
              memory
            </button>



          </div>

          {spotifyReady && !spotifyActive && (
            <div className="device-hint">veronica · {deviceId?.slice(0, 8)}…</div>
          )}

          {/* ── Memory overlay ── */}
          {showMemory && (
            <div className="overlay-panel">
              <div className="overlay-header">
                <span className="overlay-title">memory</span>
                <div className="overlay-actions">
                  <button className="overlay-action-btn" onClick={handleClearMemory} title="Clear all">clear</button>
                  <button className="overlay-close" onClick={() => setShowMemory(false)}>×</button>
                </div>
              </div>
              {memories.length === 0
                ? <p className="overlay-empty">No memories yet. Veronica learns as you talk.</p>
                : <div className="overlay-list">
                    {memories.map((m, i) => (
                      <div key={i} className="overlay-item">
                        <span className="overlay-item-cat">{m.category}</span>
                        <span className="overlay-item-text">{m.fact}</span>
                      </div>
                    ))}
                  </div>
              }
            </div>
          )}

        </div>
      </div>

      {/* Right panel */}
      <div className={`veronica-right ${spotifyActive ? 'panel-visible' : ''}`}>
        <div className="panel-divider" />
        {(spotifyError || authError) && (
          <div className="sp-err-banner">
            {authError ? `Login failed: ${authError}` : spotifyError}
          </div>
        )}
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
      </div>

      {micError && <div className="mic-error">Mic: {micError}</div>}

      <DocumentPanel
        isOpen={docPanelOpen}
        onClose={() => setDocPanelOpen(false)}
        activeDocIds={activeDocIds}
        onDocSelect={(docId) => {
            setActiveDocIds((prev) => {
              const next = new Set(prev);
              if (!docId) {
                // Clear all — clear conversation history so LLM forgets doc content
                voiceService.clearHistory();
                return new Set();
              }
              if (next.has(docId)) {
                next.delete(docId);
              } else {
                next.add(docId);
              }
              // If all docs deactivated, clear history
              if (next.size === 0) voiceService.clearHistory();
              return next;
            });
          }}
      />
    </div>
  );
}

export default VeronicaUI;
