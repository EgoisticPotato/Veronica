import React, { useMemo, useState, useCallback } from 'react';
import { voiceService } from '../services/voiceService';
import './SpotifyPlayer.css';

function formatTime(ms) {
  if (!ms || ms < 0) return '0:00';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function SpotifyPlayer({
  currentTrack, isPaused, isActive,
  position, duration,
  onTogglePlay, onNext, onPrev, onSeek,
  onVolumeChange, volume,
  queue = [],
  deviceId,
  onQueueChange,
  onPlayUri,
}) {
  const [queueInput,   setQueueInput]   = useState('');
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueMsg,     setQueueMsg]     = useState('');

  const albumArt   = currentTrack?.album?.images?.[0]?.url;
  const trackName  = currentTrack?.name  || '—';
  const artistName = currentTrack?.artists?.[0]?.name || '—';
  const albumName  = currentTrack?.album?.name || '';

  const progressPct = useMemo(() =>
    duration > 0 ? Math.min((position / duration) * 100, 100) : 0,
    [position, duration]
  );

  const handleAddToQueue = useCallback(async () => {
    const q = queueInput.trim();
    if (!q || queueLoading) return;
    setQueueLoading(true);
    setQueueMsg('');
    try {
      const r = await voiceService.addToQueue(q, deviceId);
      setQueueMsg(`✓ ${r.track_name} — ${r.artist_name}`);
      setQueueInput('');
      onQueueChange?.();
      setTimeout(() => onQueueChange?.(), 1200);
    } catch (e) {
      setQueueMsg('Could not find that track.');
    } finally {
      setQueueLoading(false);
      setTimeout(() => setQueueMsg(''), 3500);
    }
  }, [queueInput, queueLoading, deviceId, onQueueChange]);

  if (!isActive || !currentTrack) {
    return (
      <div className="sp-inactive">
        <div className="sp-inactive-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="rgba(255,255,255,0.15)">
            <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
          </svg>
        </div>
        <p className="sp-inactive-title">Nothing playing</p>
        <p className="sp-inactive-hint">Select <em>Veronica — AI Assistant</em><br/>in Spotify Connect</p>
      </div>
    );
  }

  return (
    <div className="sp-root">

      <div className="sp-art-wrap">
        {albumArt && (
          <>
            <div className="sp-art-blur" style={{ backgroundImage: `url(${albumArt})` }} />
            <img src={albumArt} alt={trackName} className="sp-art" />
          </>
        )}
      </div>

      <div className="sp-meta">
        <div className="sp-track">{trackName}</div>
        <div className="sp-artist">{artistName}</div>
        {albumName && <div className="sp-album">{albumName}</div>}
      </div>

      <div className="sp-progress-wrap">
        <span className="sp-time">{formatTime(position)}</span>
        <div
          className="sp-progress-track"
          onClick={(e) => {
            if (!onSeek || !duration) return;
            const rect = e.currentTarget.getBoundingClientRect();
            onSeek(Math.floor(((e.clientX - rect.left) / rect.width) * duration));
          }}
        >
          <div className="sp-progress-fill" style={{ width: `${progressPct}%` }} />
          <div className="sp-progress-thumb" style={{ left: `${progressPct}%` }} />
        </div>
        <span className="sp-time">{formatTime(duration)}</span>
      </div>

      <div className="sp-controls">
        <button className="sp-btn" onClick={onPrev} title="Previous">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
        </button>
        <button className="sp-btn sp-play" onClick={onTogglePlay} title={isPaused ? 'Play' : 'Pause'}>
          {isPaused
            ? <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
            : <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
          }
        </button>
        <button className="sp-btn" onClick={onNext} title="Next">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
        </button>
      </div>

      <div className="sp-volume">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="rgba(255,255,255,0.35)">
          <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/>
        </svg>
        <input
          type="range" min="0" max="1" step="0.01"
          value={volume}
          onChange={(e) => onVolumeChange(parseFloat(e.target.value))}
          className="sp-vol-slider"
        />
        <svg width="13" height="13" viewBox="0 0 24 24" fill="rgba(255,255,255,0.35)">
          <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM16.5 3A11.97 11.97 0 0 1 21 12a11.97 11.97 0 0 1-4.5 9.01v-2.26a9.48 9.48 0 0 0 0-13.5V3z"/>
        </svg>
      </div>

      {/* Add to queue */}
      <div className="sp-queue-add">
        <div className="sp-queue-add-label">add to queue</div>
        <div className="sp-queue-add-row">
          <input
            type="text"
            className="sp-queue-input"
            placeholder="search track..."
            value={queueInput}
            onChange={(e) => setQueueInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddToQueue()}
            disabled={queueLoading}
          />
          <button
            className="sp-queue-btn"
            onClick={handleAddToQueue}
            disabled={!queueInput.trim() || queueLoading}
            title="Add to queue"
          >
            {queueLoading
              ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="3" opacity="0.7"/></svg>
              : <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19 13H13v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
            }
          </button>
        </div>
        {queueMsg && <div className="sp-queue-msg">{queueMsg}</div>}
      </div>

      {/* Queue list — click to play, no drag */}
      {queue && queue.length > 0 && (
        <div className="sp-queue">
          <div className="sp-queue-title">up next</div>
          <div className="sp-queue-list">
            {queue.map((track, i) => (
              <div
                key={track.uri + i}
                className="sp-queue-item"
                onClick={() => {
                  onPlayUri?.(track.uri, i);
                  setTimeout(() => onQueueChange?.(), 800);
                }}
                title={`Play ${track.name}`}
              >
                <div className="sp-queue-art-wrap">
                  {track.album_art && (
                    <img src={track.album_art} alt="" className="sp-queue-art" />
                  )}
                  <div className="sp-queue-play-icon">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M8 5v14l11-7z"/>
                    </svg>
                  </div>
                </div>
                <div className="sp-queue-info">
                  <div className="sp-queue-name">{track.name}</div>
                  <div className="sp-queue-artist">{track.artist}</div>
                </div>
                <div className="sp-queue-dur">{formatTime(track.duration_ms)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

    </div>
  );
}

export default SpotifyPlayer;
