import React from 'react';
import './ButterflyIcon.css';

/**
 * ButterflyIcon — The new symbol for Veronica.
 * Represented as a sleek, geometric butterfly with circuit-like paths.
 * Animates with a subtle "breathing" effect when idle.
 */
const ButterflyIcon = ({ className = "" }) => {
  return (
    <div className={`butterfly-container ${className}`}>
      <svg
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="butterfly-svg"
      >
        {/* Glow Filter */}
        <defs>
          <filter id="butterfly-glow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* Central Body (Neural Node) */}
        <circle cx="50" cy="50" r="4" fill="white" filter="url(#butterfly-glow)" />

        {/* Wing Paths — Geometric & Modern */}
        <g className="butterfly-wings" filter="url(#butterfly-glow)">
          {/* Top Left Wing */}
          <path
            d="M50 50 L35 25 L15 35 L25 55 Z"
            stroke="rgba(0, 255, 242, 0.8)"
            strokeWidth="1.5"
            strokeLinejoin="round"
            className="wing-path top-left"
          />
          {/* Top Right Wing */}
          <path
            d="M50 50 L65 25 L85 35 L75 55 Z"
            stroke="rgba(157, 0, 255, 0.8)"
            strokeWidth="1.5"
            strokeLinejoin="round"
            className="wing-path top-right"
          />
          {/* Bottom Left Wing */}
          <path
            d="M50 50 L30 75 L15 65 L25 55 Z"
            stroke="rgba(0, 255, 242, 0.6)"
            strokeWidth="1.5"
            strokeLinejoin="round"
            className="wing-path bottom-left"
          />
          {/* Bottom Right Wing */}
          <path
            d="M50 50 L70 75 L85 65 L75 55 Z"
            stroke="rgba(157, 0, 255, 0.6)"
            strokeWidth="1.5"
            strokeLinejoin="round"
            className="wing-path bottom-right"
          />

          {/* Connectors (Circuit lines) */}
          <line x1="35" y1="25" x2="25" y2="55" stroke="rgba(255,255,255,0.2)" strokeWidth="0.5" />
          <line x1="65" y1="25" x2="75" y2="55" stroke="rgba(255,255,255,0.2)" strokeWidth="0.5" />
        </g>
      </svg>
    </div>
  );
};

export default ButterflyIcon;
