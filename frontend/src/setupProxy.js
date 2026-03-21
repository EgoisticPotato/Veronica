/**
 * CRA development proxy — setupProxy.js
 *
 * All /api/* requests from React (localhost:3000) are forwarded to
 * the FastAPI backend (localhost:5000). This includes:
 *   /api/v1/auth/login      → starts Spotify OAuth
 *   /api/v1/auth/callback   → Spotify redirects here after login
 *   /api/v1/auth/token      → check session
 *   /api/v1/voice/*         → STT / NLP / TTS / play
 *
 * NOTE: Only /api/* is proxied. React Router handles everything else.
 * This file is ignored in production (nginx/caddy handles proxying there).
 */

const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function (app) {
  app.use(
    '/api',
    createProxyMiddleware({
      target:       'http://127.0.0.1:5000',
      changeOrigin: true,
      logLevel:     'warn',
    })
  );
};
