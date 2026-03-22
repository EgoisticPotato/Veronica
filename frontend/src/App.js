import React, { useState, useEffect } from 'react';
import VeronicaUI from './components/VeronicaUI';
import Login from './components/Login';
import { spotifyService } from './services/spotifyService';

function App() {
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    console.log("App: Checking for token...");
    spotifyService.getToken().then((t) => {
      console.log("App: Token received:", t ? "YES" : "NO");
      setToken(t);
      setLoading(false);
    });
  }, []);

  if (loading) return null;

  return token ? <VeronicaUI token={token} /> : <Login />;
}

export default App;
