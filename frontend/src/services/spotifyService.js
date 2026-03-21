/**
 * Spotify session service — checks auth token on app mount
 */

class SpotifyService {
  async getToken() {
    try {
      const res = await fetch('/api/v1/auth/token');
      if (!res.ok) return null;
      const data = await res.json();
      return data.access_token || null;
    } catch {
      return null;
    }
  }

  async logout() {
    await fetch('/api/v1/auth/logout', { method: 'POST' });
  }
}

export const spotifyService = new SpotifyService();
