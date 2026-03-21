import React from 'react';
import './Login.css';

function Login() {
  return (
    <div className="login-root">
      <div className="login-glow" />
      <div className="login-content">
        <div className="login-logo">
          <div className="login-orb">
            <svg viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="60" cy="60" r="48" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
              <circle cx="60" cy="60" r="32" stroke="rgba(255,255,255,0.12)" strokeWidth="0.5" />
              {Array.from({ length: 32 }, (_, i) => {
                const angle = (i / 32) * Math.PI * 2;
                const r = 47;
                return (
                  <circle
                    key={i}
                    cx={60 + Math.cos(angle) * r}
                    cy={60 + Math.sin(angle) * r}
                    r={i % 4 === 0 ? 1.8 : 1}
                    fill="rgba(255,255,255,0.55)"
                    style={{
                      animation: 'pulse-dot 2.4s ease-in-out infinite',
                      animationDelay: `${i * 0.075}s`,
                    }}
                  />
                );
              })}
            </svg>
          </div>
        </div>

        <h1 className="login-title">VERONICA</h1>
        <p className="login-subtitle">AI Voice Assistant</p>

        {/* Points to /api/v1/auth/login — proxied to backend in dev, direct in prod */}
        <a className="login-btn" href="/api/v1/auth/login">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
          </svg>
          Connect with Spotify
        </a>

        <p className="login-note">Spotify Premium required for playback</p>
      </div>
    </div>
  );
}

export default Login;
