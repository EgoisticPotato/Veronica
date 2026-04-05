/**
 * Voice API service — STT, NLP, TTS, Spotify controls, document/queue management
 */

class VoiceService {
  async transcribe(audioBlob) {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    const res = await fetch('/api/v1/voice/transcribe', { method: 'POST', body: formData });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `STT ${res.status}`); }
    return (await res.json()).transcript || '';
  }

  async query(text, docIds = []) {
    // docIds: array of active doc IDs (empty = no active docs → web search)
    const res = await fetch('/api/v1/voice/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, doc_ids: docIds }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Query ${res.status}`); }
    return await res.json();
  }

  async synthesize(text) {
    const res = await fetch('/api/v1/voice/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      // Backend TTS failed — use browser Web Speech API as fallback
      console.warn('[TTS] Backend failed, using browser speech synthesis');
      return { _browserTTS: true, text };
    }
    // Backend returns "text/plain" as sentinel when it wants browser TTS
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('text/plain')) {
      return { _browserTTS: true, text };
    }
    return await res.blob();
  }

  /** Browser TTS using Web Speech API — female voice, zero config */
  speakWithBrowser(text, onDone) {
    if (!window.speechSynthesis) {
      console.warn('[TTS] window.speechSynthesis not supported');
      onDone?.();
      return () => { };
    }
    window.speechSynthesis.cancel();

    const speak = () => {
      const utt = new SpeechSynthesisUtterance(text);
      utt.rate = 1.0;
      utt.pitch = 1.1;
      utt.volume = 1.0;
      utt.lang = 'en-US';

      // Female voice keywords — checked in priority order
      const FEMALE_KW = /zira|samantha|victoria|karen|moira|fiona|google us english female|female|woman/i;
      const voices = window.speechSynthesis.getVoices();
      // Prefer en-US female, fall back to any female, then any en-US
      const voice =
        voices.find(v => v.lang.startsWith('en') && FEMALE_KW.test(v.name)) ||
        voices.find(v => FEMALE_KW.test(v.name)) ||
        voices.find(v => v.lang.startsWith('en-US')) ||
        voices.find(v => v.lang.startsWith('en'));
      if (voice) {
        utt.voice = voice;
        console.log('[TTS] Using voice:', voice.name);
      }
      utt.onend = () => onDone?.();
      utt.onerror = () => onDone?.();
      window.speechSynthesis.speak(utt);
    };

    // Voices may not be loaded yet on first call — wait for them
    const voices = window.speechSynthesis.getVoices();
    if (voices.length > 0) {
      speak();
    } else {
      window.speechSynthesis.addEventListener('voiceschanged', speak, { once: true });
    }

    return () => window.speechSynthesis.cancel();
  }

  /** Clear NLP conversation history (call when doc deactivated so model forgets doc content) */
  async clearHistory() {
    try {
      await fetch('/api/v1/voice/history', { method: 'DELETE' });
    } catch (_) { }
  }

  async playOnSpotify(query, device_id) {
    const res = await fetch('/api/v1/voice/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, device_id }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Play ${res.status}`); }
    return await res.json();
  }

  async controlMusic(action) {
    const map = {
      stop: '/api/v1/voice/pause-music', pause: '/api/v1/voice/pause-music',
      resume: '/api/v1/voice/resume-music', next: '/api/v1/voice/next-track',
      previous: '/api/v1/voice/previous-track',
    };
    const endpoint = map[action];
    if (!endpoint) throw new Error(`Unknown music action: ${action}`);
    const res = await fetch(endpoint, { method: 'POST' });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Music ${res.status}`); }
    return await res.json();
  }

  async fetchQueue() {
    const res = await fetch('/api/v1/music/queue');
    if (!res.ok) return { currently_playing: null, queue: [] };
    return await res.json();
  }

  async addToQueue(query, device_id) {
    const res = await fetch('/api/v1/music/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, device_id: device_id || '' }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Queue ${res.status}`); }
    return await res.json();
  }

  /**
   * Add a track to queue by URI (used during drag-to-reorder re-queuing)
   * @param {string} uri  — spotify:track:xxx
   * @param {string} device_id
   */
  async addUriToQueue(uri, device_id) {
    const res = await fetch('/api/v1/music/queue-uri', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri, device_id: device_id || '' }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `QueueURI ${res.status}`); }
    return await res.json();
  }

  /** Capture screen and analyse it via vision LLM */
  async analyseScreenshot(question = "What do you see in this screenshot?") {
    // 1. Ask user to select screen/window
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 1 } },
        audio: false,
      });
    } catch (e) {
      throw new Error("Screen capture cancelled or permission denied.");
    }

    let imageB64;
    try {
      // 2. Try ImageCapture API first — most reliable for single frame grabs
      const track = stream.getVideoTracks()[0];
      if (typeof ImageCapture !== "undefined") {
        const capture = new ImageCapture(track);
        // grabFrame() returns an ImageBitmap of the exact current frame
        const bitmap = await capture.grabFrame();
        const canvas = document.createElement("canvas");
        const MAX_W = 1280;
        const scale = bitmap.width > MAX_W ? MAX_W / bitmap.width : 1;
        canvas.width = Math.round(bitmap.width * scale);
        canvas.height = Math.round(bitmap.height * scale);
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        bitmap.close();
        imageB64 = canvas.toDataURL("image/jpeg", 0.70).split(",")[1];
      } else {
        // 3. Fallback: video element approach with longer settle time
        const video = document.createElement("video");
        video.srcObject = stream;
        video.muted = true;
        // Must be in DOM for Chrome to decode frames off-screen
        video.style.cssText = "position:fixed;top:-9999px;left:-9999px;width:1px;height:1px;";
        document.body.appendChild(video);

        await new Promise((resolve, reject) => {
          video.onloadedmetadata = () => video.play().then(resolve).catch(reject);
          video.onerror = reject;
          setTimeout(reject, 5000); // 5s timeout
        });

        // Wait for actual frame data — poll until dimensions are non-zero
        await new Promise((r) => {
          let attempts = 0;
          const check = () => {
            if (video.videoWidth > 0 && video.videoHeight > 0) return r();
            if (++attempts > 30) return r(); // max 1.5s wait
            setTimeout(check, 50);
          };
          check();
        });

        // Extra settle — ensure frame is painted
        await new Promise((r) => setTimeout(r, 300));

        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth || 1280;
        canvas.height = video.videoHeight || 720;
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        document.body.removeChild(video);
        imageB64 = canvas.toDataURL("image/jpeg", 0.70).split(",")[1];
      }
    } finally {
      // Always stop the stream regardless of success/failure
      stream.getTracks().forEach((t) => t.stop());
    }

    if (!imageB64) throw new Error("Could not capture screen frame.");

    const res = await fetch("/api/v1/vision/screenshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_b64: imageB64, question }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || `Screenshot ${res.status}`);
    }
    return (await res.json()).description;
  }

  /** Fetch lyrics for a track */
  async getLyrics(track, artist = "") {
    const params = new URLSearchParams({ track, artist });
    const res = await fetch(`/api/v1/music/lyrics?${params}`);
    if (!res.ok) return { lyrics: "", source: "" };
    return await res.json();
  }

  /** Memory API */
  async getMemory() {
    const res = await fetch("/api/v1/memory");
    if (!res.ok) return { memories: [] };
    return await res.json();
  }
  async addMemory(fact, category = "general") {
    const res = await fetch("/api/v1/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fact, category }),
    });
    if (!res.ok) throw new Error("Could not save memory");
    return await res.json();
  }
  async clearMemory() {
    await fetch("/api/v1/memory", { method: "DELETE" });
  }

  /**
   * Play audio blob with Web Audio API frequency analyser.
   * Returns a stop() function for barge-in interruption.
   */
  async playAudioWithAnalyzer(blob, onFrame, onEnd) {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') await audioCtx.resume();

    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
    const source = audioCtx.createBufferSource();
    source.buffer = audioBuffer;

    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);

    source.connect(analyser);
    analyser.connect(audioCtx.destination);
    source.start(0);

    let animFrame;
    let stopped = false;

    const tick = () => {
      analyser.getByteFrequencyData(dataArray);
      onFrame(dataArray);
      animFrame = requestAnimationFrame(tick);
    };
    tick();

    source.onended = () => {
      if (stopped) return;
      cancelAnimationFrame(animFrame);
      audioCtx.close();
      onEnd?.();
    };

    const stop = () => {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(animFrame);
      try { source.stop(); } catch (_) { }
      audioCtx.close();
      onEnd?.();
    };

    return stop;
  }

  /**
   * Run the agentic pipeline — POST goal, read SSE stream.
   * @param {string} goal — natural language goal
   * @param {function} onEvent — called for each SSE event: { type, ... }
   * @returns {Promise<void>} resolves when stream is done
   */
  async runAgent(goal, onEvent) {
    const res = await fetch('/api/v1/voice/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || `Agent ${res.status}`);
    }

    // Read SSE stream via ReadableStream (EventSource can't POST)
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE lines: "data: {...}\n\n"
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep incomplete line in buffer

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data: ')) {
          try {
            const event = JSON.parse(trimmed.slice(6));
            onEvent?.(event);
          } catch (e) {
            console.warn('[Agent] Could not parse SSE:', trimmed);
          }
        }
      }
    }

    // Process remaining buffer
    if (buffer.trim().startsWith('data: ')) {
      try {
        const event = JSON.parse(buffer.trim().slice(6));
        onEvent?.(event);
      } catch (_) {}
    }
  }

  /**
   * Analyze current screen via server-side capture (mss + Ollama vision).
   * @param {string} question — what to ask about the screen
   * @returns {Promise<string>} description
   */
  async analyzeScreen(question = 'What is on my screen?') {
    const res = await fetch('/api/v1/voice/vision', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || `Vision ${res.status}`);
    }
    return (await res.json()).description;
  }
}

export const voiceService = new VoiceService();