document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  const spotifyId = urlParams.get('spotify_id');
  const displayName = urlParams.get('display_name');
  
  const titleEl = document.getElementById('title');
  const messageEl = document.getElementById('message');
  const closeBtn = document.getElementById('close-btn');

  // Programmatic click handler for Close Button (fixing CSP rules)
  closeBtn.addEventListener('click', () => {
    window.close();
  });

  if (spotifyId) {
    const sessionData = { 
      spotify_id: spotifyId,
      display_name: displayName || spotifyId,
      logged_in: true
    };

    const saveSuccess = () => {
      console.log('Successfully saved user session details.');
      
      titleEl.textContent = 'Successfully Connected!';
      titleEl.style.backgroundImage = 'linear-gradient(135deg, #1DB954 0%, #a5dca2 100%)';
      titleEl.style.webkitBackgroundClip = 'text';
      titleEl.style.webkitTextFillColor = 'transparent';
      
      messageEl.textContent = `Welcome, ${displayName || 'Music Lover'}! Your Spotify profile is securely connected. You may close this tab now and click the extension icon to start generating AI playlists.`;
      
      // Show close button just in case auto-close fails
      closeBtn.style.display = 'inline-block';
      
      // Auto-close after 2 seconds
      setTimeout(() => {
        window.close();
      }, 2000);
    };

    // Environment-agnostic storage helper
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.set(sessionData, saveSuccess);
    } else {
      Object.keys(sessionData).forEach(k => {
        localStorage.setItem(k, sessionData[k]);
      });
      saveSuccess();
    }
  } else {
    titleEl.textContent = 'Connection Failed';
    titleEl.style.color = '#ef4444';
    messageEl.textContent = 'Could not retrieve Spotify profile credentials. Please close this window and try connecting again.';
    closeBtn.style.display = 'inline-block';
    closeBtn.textContent = 'Go Back';
    closeBtn.style.background = 'linear-gradient(135deg, #ef4444 0%, #b91c1c 100%)';
    closeBtn.style.boxShadow = '0 10px 25px rgba(239, 68, 68, 0.3)';
  }
});
