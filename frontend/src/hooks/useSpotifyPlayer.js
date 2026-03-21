import { useState, useEffect, useRef, useCallback } from 'react';

/**
 * useSpotifyPlayer
 *
 * Hybrid approach:
 *   - Spotify Web Playback SDK: registers a device, receives state events
 *   - Spotify Web API (/me/player/*): all controls (play/pause/next/prev/seek/volume)
 *
 * Why: SDK controls (player.togglePlay() etc.) silently fail when:
 *   1. React StrictMode double-mounts cause onSpotifyWebPlaybackSDKReady to fire
 *      before playerRef is set on the second mount
 *   2. The SDK WebSocket drops and playerRef.current becomes stale
 *   3. The device isn't the active transfer target yet
 *
 * Web API calls are plain fetch() — always visible in DevTools Network tab,
 * always reliable, no SDK state dependency.
 */

const SPOTIFY_API = 'https://api.spotify.com/v1/me/player';

export function useSpotifyPlayer(token) {
  const [deviceId,     setDeviceId]     = useState(null);
  const [isReady,      setIsReady]      = useState(false);
  const [isActive,     setIsActive]     = useState(false);
  const [isPaused,     setIsPaused]     = useState(true);
  const [currentTrack, setCurrentTrack] = useState(null);
  const [position,     setPosition]     = useState(0);
  const [duration,     setDuration]     = useState(0);
  const [volume,       setVolumeState]  = useState(0.5);
  const [error,        setError]        = useState(null);
  const [queue,        setQueue]        = useState([]);
  // localQueue is the drag-reordered view; null means use the real Spotify queue
  const [localQueue,   setLocalQueue]   = useState(null);

  const playerRef  = useRef(null);
  const tokenRef   = useRef(token);   // always-current token for API calls
  const tickRef    = useRef(null);
  const pausedRef  = useRef(true);
  const deviceRef  = useRef(null);    // always-current deviceId for API calls

  // Keep refs in sync so callbacks always have current values
  useEffect(() => { tokenRef.current = token; }, [token]);
  useEffect(() => { deviceRef.current = deviceId; }, [deviceId]);

  // ── Web API helper ────────────────────────────────────────────────────────
  const apiCall = useCallback(async (method, path, body = null) => {
    const t = tokenRef.current;
    if (!t) { console.error('No Spotify token'); return; }

    const opts = {
      method,
      headers: { Authorization: `Bearer ${t}` },
    };
    if (body !== null) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }

    const res = await fetch(`${SPOTIFY_API}${path}`, opts);

    // 204 No Content = success for most control endpoints
    if (res.status === 204 || res.status === 200) return res;

    // 403 = not Premium, 404 = no active device
    const text = await res.text().catch(() => '');
    console.error(`Spotify API ${method} ${path} → ${res.status}:`, text);
    throw new Error(`Spotify API error ${res.status}`);
  }, []);

  // ── Position tick ─────────────────────────────────────────────────────────
  const startTick = useCallback((startPos) => {
    clearInterval(tickRef.current);
    pausedRef.current = false;
    setPosition(startPos);
    tickRef.current = setInterval(() => {
      if (!pausedRef.current) setPosition((p) => p + 500);
    }, 500);
  }, []);

  const stopTick = useCallback(() => {
    clearInterval(tickRef.current);
    pausedRef.current = true;
  }, []);

  // ── SDK setup — only for device registration + state events ──────────────
  useEffect(() => {
    if (!token) return;

    // Guard: if SDK script already loaded, don't add it again
    const existingScript = document.querySelector(
      'script[src="https://sdk.scdn.co/spotify-player.js"]'
    );
    const script = existingScript || document.createElement('script');
    if (!existingScript) {
      script.src   = 'https://sdk.scdn.co/spotify-player.js';
      script.async = true;
      document.body.appendChild(script);
    }

    const initPlayer = () => {
      // Don't create a second player if one already exists
      if (playerRef.current) return;

      const player = new window.Spotify.Player({
        name:               'Veronica — AI Assistant',
        getOAuthToken:      (cb) => cb(tokenRef.current),
        volume:             0.5,
        enableMediaSession: true,
      });

      playerRef.current = player;

      player.on('initialization_error', ({ message }) => setError(`Init: ${message}`));
      player.on('authentication_error', ({ message }) => setError(`Auth: ${message}`));
      player.on('account_error',        ({ message }) => setError(`Spotify Premium required`));
      player.on('playback_error',       ({ message }) => console.warn('Playback:', message));

      player.addListener('ready', ({ device_id }) => {
        setDeviceId(device_id);
        setIsReady(true);
        setError(null);
        console.log('[Veronica] Spotify device ready:', device_id.slice(0, 8));
        // Fetch actual Spotify volume so our state matches reality
        player.getVolume().then((v) => {
          if (typeof v === 'number') setVolumeState(v);
        }).catch(() => {});
      });

      player.addListener('not_ready', () => setIsReady(false));

      player.addListener('player_state_changed', (state) => {
        if (!state) { setIsActive(false); stopTick(); return; }

        setCurrentTrack(state.track_window.current_track);
        setDuration(state.duration);
        setIsPaused(state.paused);
        pausedRef.current = state.paused;

        if (!state.paused) {
          startTick(state.position);
        } else {
          stopTick();
          setPosition(state.position);
        }

        // setIsActive based on whether we have a live state
        setIsActive(true);
      });

      player.connect().then((ok) => {
        if (!ok) setError('Failed to connect to Spotify');
      });

      // Queue is fetched on-demand only (via refreshQueue) — no polling
    };

    // SDK may already be loaded (hot reload / StrictMode second mount)
    if (window.Spotify?.Player) {
      initPlayer();
    } else {
      window.onSpotifyWebPlaybackSDKReady = initPlayer;
    }

    return () => {
      stopTick();
      if (playerRef.current) {
        playerRef.current.disconnect();
        playerRef.current = null;
      }
    };
  }, [token, startTick, stopTick]);

  // ── Controls — ALL use Spotify Web API, not SDK methods ──────────────────

  const togglePlay = useCallback(async () => {
    try {
      if (isPaused) {
        // Resume — pass device_id so Spotify knows which device
        const d = deviceRef.current;
        await apiCall('PUT', '/play', d ? { device_id: d } : {});
      } else {
        await apiCall('PUT', '/pause');
      }
    } catch (e) {
      console.error('togglePlay failed:', e.message);
    }
  }, [isPaused, apiCall]);

  const nextTrack = useCallback(async () => {
    try {
      await apiCall('POST', '/next');
    } catch (e) {
      console.error('nextTrack failed:', e.message);
    }
  }, [apiCall]);

  const previousTrack = useCallback(async () => {
    try {
      await apiCall('POST', '/previous');
    } catch (e) {
      console.error('previousTrack failed:', e.message);
    }
  }, [apiCall]);

  const seek = useCallback(async (ms) => {
    try {
      setPosition(ms); // optimistic update
      await apiCall('PUT', `/seek?position_ms=${Math.floor(ms)}`);
    } catch (e) {
      console.error('seek failed:', e.message);
    }
  }, [apiCall]);

  const playUri = useCallback(async (uri, queueIndex) => {
    try {
      const d = deviceRef.current;
      const body = { uris: [uri] };
      if (d) body.device_id = d;
      await apiCall('PUT', '/play', body);
      // Optimistically trim displayed queue — show only tracks after clicked one
      if (typeof queueIndex === 'number') {
        setQueue((prev) => prev.slice(queueIndex + 1));
      }
    } catch (e) {
      console.error('playUri failed:', e.message);
    }
  }, [apiCall]);

  const setVolume = useCallback(async (val) => {
    try {
      setVolumeState(val); // optimistic update
      const pct = Math.round(val * 100);
      await apiCall('PUT', `/volume?volume_percent=${pct}`);
    } catch (e) {
      console.error('setVolume failed:', e.message);
    }
  }, [apiCall]);

  const activateElement = useCallback(() => {
    playerRef.current?.activateElement();
  }, []);

  // Call this after adding a track to queue to get immediate UI update
  const refreshQueue = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/music/queue');
      if (res.ok) {
        const data = await res.json();
        setQueue(data.queue || []);
        setLocalQueue(null); // clear any local drag-reorder on real refresh
      }
    } catch (_) {}
  }, []);

  // Accept a locally reordered queue from drag-and-drop in SpotifyPlayer
  const handleLocalQueueReorder = useCallback((reordered) => {
    setLocalQueue(reordered);
  }, []);

  return {
    deviceId, isReady, isActive, isPaused,
    currentTrack, position, duration, volume, error,
    queue: localQueue ?? queue,   // drag-reordered view or real Spotify queue
    refreshQueue, handleLocalQueueReorder,
    togglePlay, nextTrack, previousTrack, seek, setVolume, activateElement, playUri,
  };
}
