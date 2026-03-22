/**
 * Spotify session service — checks auth token on app mount
 */

class SpotifyService {
  async getToken() {
    try {
      const res = await fetch('/api/v1/auth/token');
      console.log("SpotifyService: GET /api/v1/auth/token status:", res.status);
      if (!res.ok) {
        console.warn("SpotifyService: Token check failed (not ok)");
        return null;
      }
      const data = await res.json();
      console.log("SpotifyService: Data received:", data);
      return data.access_token || null;
    } catch (err) {
      console.error("SpotifyService: getToken error:", err);
      return null;
    }
  }

  async logout() {
    await fetch('/api/v1/auth/logout', { method: 'POST' });
  }
}

export const spotifyService = new SpotifyService();
