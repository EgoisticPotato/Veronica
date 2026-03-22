import { useEffect, useRef, useCallback } from 'react';

// ─── Neural Matrix Constants ──────────────────────────────────────────────────
const NODE_COUNT  = 160;
const CONNECT_DIST = 180;

// State → colors
function getStateColor(state) {
  switch (state) {
    case 'listening':  return { r: 6,   g: 182, b: 212 };  // cyan
    case 'processing': return { r: 245, g: 158, b: 11  };  // amber
    case 'speaking':   return { r: 16,  g: 185, b: 129 };  // green
    default:           return { r: 124, g: 58,  b: 237 };  // purple (idle)
  }
}

// ─── Hook ──────────────────────────────────────────────────────────────────────

export function useParticleEngine(canvasRef, state) {
  const stateRef    = useRef(state);
  const nodesRef    = useRef([]);
  const freqAmpsRef = useRef(new Float32Array(NODE_COUNT));
  const rafRef      = useRef(null);
  const engineRef   = useRef({ W: 0, H: 0, ready: false });

  useEffect(() => { stateRef.current = state; }, [state]);

  // ── Boot engine ───────────────────────────────────────────────────────────
  const bootEngine = useCallback((canvas) => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    const W = canvas.width;
    const H = canvas.height;
    if (W === 0 || H === 0) return;

    engineRef.current = { W, H, ready: true };

    // Seed nodes randomly
    nodesRef.current = Array.from({ length: NODE_COUNT }, () => ({
      x:  Math.random() * W,
      y:  Math.random() * H,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      r:  1.2 + Math.random() * 1.5,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: 0.01 + Math.random() * 0.025,
    }));

    const ctx = canvas.getContext('2d');

    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      const c = getStateColor(stateRef.current);
      const isActive = stateRef.current !== 'idle';
      const speed = isActive ? 2 : 1;
      const fa = freqAmpsRef.current;
      const freqAvg = fa.reduce((a, b) => a + b, 0) / fa.length;
      const boost = isActive ? (1 + freqAvg * 3) : 1;
      const nodes = nodesRef.current;

      nodes.forEach((n, i) => {
        n.pulse += n.pulseSpeed;
        const pulseAlpha = (Math.sin(n.pulse) + 1) / 2;

        // Move
        n.x += n.vx * speed * boost;
        n.y += n.vy * speed * boost;
        if (n.x < 0) n.x = W;
        if (n.x > W) n.x = 0;
        if (n.y < 0) n.y = H;
        if (n.y > H) n.y = 0;

        // Draw edges
        for (let j = i + 1; j < nodes.length; j++) {
          const n2 = nodes[j];
          const dx = n.x - n2.x;
          const dy = n.y - n2.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < CONNECT_DIST) {
            const alpha = (1 - dist / CONNECT_DIST) * 0.15 * (isActive ? 1.8 : 1);
            ctx.strokeStyle = `rgba(${c.r},${c.g},${c.b},${alpha})`;
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(n.x, n.y);
            ctx.lineTo(n2.x, n2.y);
            ctx.stroke();
          }
        }

        // Draw node
        const nodeR = n.r * (isActive ? (1 + pulseAlpha * 0.4 * boost) : 1);
        const nodeAlpha = 0.3 + pulseAlpha * 0.4;
        ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${nodeAlpha})`;
        ctx.beginPath();
        ctx.arc(n.x, n.y, nodeR, 0, Math.PI * 2);
        ctx.fill();
      });

      // Decay frequency amps
      for (let i = 0; i < NODE_COUNT; i++) fa[i] *= 0.92;

      rafRef.current = requestAnimationFrame(draw);
    };

    draw();
  }, []);

  // ── ResizeObserver ────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        const w = Math.floor(width);
        const h = Math.floor(height);
        if (w > 0 && h > 0) {
          canvas.width  = w;
          canvas.height = h;
          bootEngine(canvas);
        }
      }
    });

    ro.observe(canvas);
    return () => {
      ro.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [canvasRef, bootEngine]);

  // ── Push real audio frequency data ────────────────────────────────────────
  const pushFrequencyData = useCallback((uint8Array) => {
    const fa = freqAmpsRef.current;
    const len = Math.min(uint8Array.length, NODE_COUNT);
    for (let i = 0; i < len; i++) {
      fa[i] = uint8Array[i] / 8; // 0-255 → 0-32
    }
  }, []);

  return { pushFrequencyData };
}
