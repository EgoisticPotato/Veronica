import { useEffect, useRef, useCallback } from 'react';
import anime from 'animejs';

// ─── Constants ────────────────────────────────────────────────────────────────
const N              = 500;
const BASE_RADIUS    = 110;
const SPEAKING_DRIFT = 1.4;

// ─── Shape generators ─────────────────────────────────────────────────────────

function circleTargets(cx, cy, r, count) {
  return Array.from({ length: count }, (_, i) => ({
    x: cx + Math.cos((i / count) * Math.PI * 2) * r,
    y: cy + Math.sin((i / count) * Math.PI * 2) * r,
  }));
}

function questionMarkTargets(cx, cy, count) {
  const pts = [];

  const arcOuter = Math.floor(count * 0.22);
  for (let k = 0; k < arcOuter; k++) {
    const a = (-0.1 + (k / arcOuter) * 1.5) * Math.PI;
    pts.push({ x: cx + 10 + Math.cos(a) * 64, y: cy - 30 + Math.sin(a) * 58 });
  }

  const arcInner = Math.floor(count * 0.14);
  for (let k = 0; k < arcInner; k++) {
    const a = (1.3 - (k / arcInner) * 1.1) * Math.PI;
    pts.push({ x: cx + 10 + Math.cos(a) * 34, y: cy - 20 + Math.sin(a) * 32 });
  }

  const tail = Math.floor(count * 0.20);
  for (let k = 0; k < tail; k++) {
    const a = (0.48 + (k / tail) * 0.7) * Math.PI;
    const r = 32 - k * 0.25;
    pts.push({ x: cx + 10 + Math.cos(a) * r, y: cy + 14 + Math.sin(a) * r * 0.9 });
  }

  const stem = Math.floor(count * 0.10);
  for (let k = 0; k < stem; k++) {
    pts.push({ x: cx + 10 + (Math.random() - 0.5) * 4, y: cy + 30 + (k / stem) * 22 });
  }

  const dot = count - pts.length;
  for (let k = 0; k < dot; k++) {
    const ang = (k / dot) * Math.PI * 2;
    pts.push({
      x: cx + 10 + Math.cos(ang) * (6 + Math.random() * 3),
      y: cy + 74 + Math.sin(ang) * (6 + Math.random() * 3),
    });
  }

  return pts.slice(0, count);
}

// Golden-ratio phyllotaxis — even spread across the full canvas
function idleTargets(W, H, count) {
  const goldenAngle = 2.399963;
  return Array.from({ length: count }, (_, i) => {
    const r     = Math.sqrt((i + 0.5) / count) * (Math.min(W, H) * 0.47);
    const angle = i * goldenAngle;
    return {
      x:           W / 2 + Math.cos(angle) * r,
      y:           H / 2 + Math.sin(angle) * r,
      driftPhaseX: Math.random() * Math.PI * 2,
      driftPhaseY: Math.random() * Math.PI * 2,
      driftSpeedX: 0.25 + Math.random() * 0.4,
      driftSpeedY: 0.25 + Math.random() * 0.4,
      driftAmp:    10 + Math.random() * 22,
    };
  });
}

// ─── Hook ──────────────────────────────────────────────────────────────────────

export function useParticleEngine(canvasRef, state) {
  const stateRef     = useRef(state);
  const particlesRef = useRef([]);
  const freqAmpsRef  = useRef(new Float32Array(N));
  const animsRef     = useRef([]);
  const rafRef       = useRef(null);
  const tRef         = useRef(0);
  const idlePosRef   = useRef([]);
  const engineRef    = useRef({ W: 0, H: 0, CX: 0, CY: 0, ready: false });

  useEffect(() => { stateRef.current = state; }, [state]);

  // ── Morph ──────────────────────────────────────────────────────────────────
  const morphTo = useCallback((targets, opts = {}) => {
    animsRef.current.forEach((a) => a.pause?.());
    animsRef.current = [];
    const duration = opts.duration ?? 1000;
    const easing   = opts.easing   ?? 'easeInOutCubic';

    particlesRef.current.forEach((p, i) => {
      const tgt   = targets[i] ?? targets[targets.length - 1];
      const proxy = { x: p.tx, y: p.ty };
      const anim  = anime({
        targets: proxy,
        x: tgt.x, y: tgt.y,
        duration: duration + Math.random() * 350,
        delay:    Math.random() * 200,
        easing,
        update: () => { p.tx = proxy.x; p.ty = proxy.y; },
      });
      animsRef.current.push(anim);
    });
  }, []);

  // ── Boot engine with real canvas dimensions ────────────────────────────────
  // Called by ResizeObserver whenever the canvas element has non-zero size.
  const bootEngine = useCallback((canvas) => {
    const eng = engineRef.current;

    // Cancel any existing RAF
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    const W  = canvas.width;
    const H  = canvas.height;
    if (W === 0 || H === 0) return;

    const CX = W / 2;
    const CY = H / 2;
    eng.W = W; eng.H = H; eng.CX = CX; eng.CY = CY; eng.ready = true;

    // Fresh idle positions for this canvas size
    idlePosRef.current = idleTargets(W, H, N);

    // Seed particles spread across canvas
    particlesRef.current = Array.from({ length: N }, (_, i) => {
      const idle = idlePosRef.current[i];
      return {
        i,
        x:  idle.x, y:  idle.y,
        tx: idle.x, ty: idle.y,
        baseOpacity: 0.4 + Math.random() * 0.45,
        baseSize:    0.9 + Math.random() * 1.6,
        driftPhaseX: idle.driftPhaseX,
        driftPhaseY: idle.driftPhaseY,
        driftSpeedX: idle.driftSpeedX,
        driftSpeedY: idle.driftSpeedY,
        driftAmp:    idle.driftAmp,
      };
    });

    // Cancel pending anime anims (stale from old canvas)
    animsRef.current.forEach((a) => a.pause?.());
    animsRef.current = [];

    // Apply current state targets immediately
    const s = stateRef.current;
    if (s === 'listening') {
      setTimeout(() => morphTo(questionMarkTargets(CX, CY, N), { duration: 800 }), 50);
    } else if (s === 'speaking') {
      setTimeout(() => morphTo(circleTargets(CX, CY, BASE_RADIUS, N), { duration: 600 }), 50);
    }
    // idle: particles are already at idle positions

    // ── Render loop ────────────────────────────────────────────────────────
    const ctx = canvas.getContext('2d');

    const draw = () => {
      tRef.current += 0.016;
      const t  = tRef.current;
      const s  = stateRef.current;
      const fa = freqAmpsRef.current;

      // Frequency update
      if (s === 'speaking') {
        for (let i = 0; i < N; i++) {
          const tgt = Math.random() < 0.15 ? (Math.random() * 44 + 6) : fa[i] * 0.91;
          fa[i] += (tgt - fa[i]) * 0.22;
        }
      } else {
        for (let i = 0; i < N; i++) fa[i] *= 0.84;
      }

      ctx.clearRect(0, 0, W, H);

      const particles = particlesRef.current;
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        // Lerp toward target
        p.x += (p.tx - p.x) * 0.10;
        p.y += (p.ty - p.y) * 0.10;

        const dx  = p.x - CX;
        const dy  = p.y - CY;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const nx  = dx / len;
        const ny  = dy / len;

        let fx = p.x;
        let fy = p.y;

        if (s === 'idle') {
          // Independent sinusoidal drift — floating matter feel
          fx = p.x + Math.sin(t * p.driftSpeedX + p.driftPhaseX) * p.driftAmp * 0.55;
          fy = p.y + Math.cos(t * p.driftSpeedY + p.driftPhaseY) * p.driftAmp * 0.55;
        } else if (s === 'listening') {
          // Shimmer on question mark shape
          fx = p.x + Math.sin(t * 3.2 + i * 0.18) * 1.8;
          fy = p.y + Math.cos(t * 2.7 + i * 0.14) * 1.8;
        } else if (s === 'speaking') {
          // Real frequency drives radial pulse
          const amp = fa[i] || 0;
          fx = p.x + nx * (amp * SPEAKING_DRIFT + Math.sin(t * 2.4 + i * 0.09) * 2);
          fy = p.y + ny * (amp * SPEAKING_DRIFT + Math.cos(t * 2.4 + i * 0.09) * 2);
        }

        // Opacity & size
        let alpha, sz;
        if (s === 'speaking') {
          const amp = fa[i] || 0;
          alpha = Math.min(0.35 + amp / 48, 1.0);
          sz    = p.baseSize * (1.0 + amp / 22);
        } else if (s === 'listening') {
          alpha = 0.62 + Math.sin(t * 2.2 + i * 0.3) * 0.22;
          sz    = p.baseSize;
        } else {
          // Idle: individual twinkle
          alpha = p.baseOpacity * (0.65 + Math.sin(t * p.driftSpeedX * 1.5 + p.driftPhaseX) * 0.35);
          sz    = p.baseSize;
        }

        ctx.beginPath();
        ctx.arc(fx, fy, Math.max(sz, 0.3), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,255,255,${Math.min(alpha, 1).toFixed(3)})`;
        ctx.fill();
      }

      rafRef.current = requestAnimationFrame(draw);
    };

    draw();
  }, [morphTo]);

  // ── ResizeObserver — boots/reboots engine when canvas gets real dimensions ─
  // This is the key fix: we NEVER read canvas.width/height at component mount
  // (they're 0 then). We wait for the browser to lay out the canvas, then boot.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        const w = Math.floor(width);
        const h = Math.floor(height);
        if (w > 0 && h > 0) {
          // Set the canvas drawing buffer to match layout size
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

  // ── State changes → morph ──────────────────────────────────────────────────
  useEffect(() => {
    if (!engineRef.current.ready) return;
    const { CX, CY } = engineRef.current;

    if (state === 'idle') {
      morphTo(idlePosRef.current, { duration: 1200, easing: 'easeInOutCubic' });
    } else if (state === 'listening') {
      morphTo(questionMarkTargets(CX, CY, N), { duration: 800, easing: 'easeInOutQuart' });
    } else if (state === 'speaking') {
      morphTo(circleTargets(CX, CY, BASE_RADIUS, N), { duration: 700, easing: 'easeOutCubic' });
    }
  }, [state, morphTo]);

  // ── Push real audio frequency data ────────────────────────────────────────
  const pushFrequencyData = useCallback((uint8Array) => {
    const fa = freqAmpsRef.current;
    const len = Math.min(uint8Array.length, N);
    for (let i = 0; i < len; i++) {
      fa[i] = uint8Array[i] / 4; // 0-255 → 0-64
    }
  }, []);

  return { pushFrequencyData };
}
