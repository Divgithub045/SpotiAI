// Base URL of the FastAPI backend
const BACKEND_URL = 'http://localhost:8000';

// Safe chrome environment wrapper polyfills (for running as a standard web tab or loaded extension)
const safeStorage = {
  get: (keys, callback) => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(keys, callback);
    } else {
      const result = {};
      keys.forEach(k => {
        const item = localStorage.getItem(k);
        if (item === 'true') {
          result[k] = true;
        } else if (item === 'false') {
          result[k] = false;
        } else {
          result[k] = item;
        }
      });
      callback(result);
    }
  },
  set: (items, callback) => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.set(items, callback);
    } else {
      Object.keys(items).forEach(k => {
        localStorage.setItem(k, items[k]);
      });
      if (callback) callback();
    }
  },
  clear: (callback) => {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.clear(callback);
    } else {
      localStorage.clear();
      if (callback) callback();
    }
  }
};

const safeTabs = {
  create: (options, callback) => {
    if (typeof chrome !== 'undefined' && chrome.tabs && chrome.tabs.create) {
      chrome.tabs.create(options, callback);
    } else {
      const win = window.open(options.url, '_blank');
      if (callback) callback(win);
    }
  }
};

// Global variables
let spotifyId = null;
let currentPlaylistId = null; // Used for regeneration context
let loadingInterval = null;

document.addEventListener('DOMContentLoaded', () => {
  initApp();
  setupEventListeners();
});

// ==========================================================================
// Initialization & Authentication Checking
// ==========================================================================
function initApp() {
  safeStorage.get(['spotify_id', 'display_name', 'logged_in'], (result) => {
    if (result.logged_in && result.spotify_id) {
      resumeSession(result);
    } else {
      showScreen('screen-login');
    }
  });
}

function resumeSession(result) {
  spotifyId = result.spotify_id;
  document.getElementById('user-name').textContent = result.display_name || 'Music Fan';
  checkUserStatusBackend(result.spotify_id);
  showScreen('screen-main');
  loadHistory();
}

function checkUserStatusBackend(id) {
  fetch(`${BACKEND_URL}/api/user/${id}`)
    .then(resp => resp.json())
    .then(data => {
      if (!data.logged_in) {
        // Backend doesn't know this user (maybe server restarted/DB wiped), clean up
        forceLogout();
      }
    })
    .catch(err => {
      console.warn('Backend connection issue during status verify:', err);
    });
}

function forceLogout() {
  safeStorage.clear(() => {
    spotifyId = null;
    showScreen('screen-login');
  });
}

// ==========================================================================
// Navigation & Router Helpers
// ==========================================================================
function showScreen(screenId) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.remove('active');
  });
  const target = document.getElementById(screenId);
  if (target) {
    target.classList.add('active');
  }
}

function switchTab(tabId, buttonElement) {
  // Update nav buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('active');
  });
  buttonElement.classList.add('active');

  // Update tab panes
  document.querySelectorAll('.tab-pane').forEach(pane => {
    pane.classList.remove('active');
  });
  document.getElementById(tabId).classList.add('active');

  // Fetch history if switching to history
  if (tabId === 'tab-history') {
    loadHistory();
  }
}

// ==========================================================================
// Action / Event Handler Wiring
// ==========================================================================
function setupEventListeners() {
  // Login Button
  document.getElementById('btn-login').addEventListener('click', handleSpotifyLogin);
  
  // Logout Button
  document.getElementById('btn-logout').addEventListener('click', forceLogout);

  // Tabs navigation
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const tabId = e.target.getAttribute('data-tab');
      switchTab(tabId, e.target);
    });
  });

  // Prompt Mode button click
  document.getElementById('btn-generate-prompt').addEventListener('click', handlePromptGeneration);

  // Guided Mode button click
  document.getElementById('btn-generate-guided').addEventListener('click', handleGuidedGeneration);

  // Back button from Result screen
  document.getElementById('btn-start-over').addEventListener('click', () => {
    showScreen('screen-main');
    loadHistory();
  });

  // Open in Spotify button from Result screen
  document.getElementById('btn-open-spotify').addEventListener('click', () => {
    if (currentPlaylistId) {
      safeTabs.create({ url: `https://open.spotify.com/playlist/${currentPlaylistId}` });
    }
  });

  // Regenerate Current playlist button
  document.getElementById('btn-regenerate-current').addEventListener('click', handleRegenerateCurrent);
}

// ==========================================================================
// Authentication Action
// ==========================================================================
function handleSpotifyLogin() {
  const extensionId = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) ? chrome.runtime.id : 'web';
  const loginUrl = `${BACKEND_URL}/api/login?extension_id=${extensionId}`;
  
  // Open login tab
  safeTabs.create({ url: loginUrl }, () => {
    // Poll chrome.storage.local to detect login completion (written by redirect callback page)
    const interval = setInterval(() => {
      safeStorage.get(['logged_in', 'spotify_id'], (result) => {
        if (result.logged_in && result.spotify_id) {
          clearInterval(interval);
          initApp();
        }
      });
    }, 1500);
  });
}

// ==========================================================================
// Playlist Curation Requests (API Calls)
// ==========================================================================
function handlePromptGeneration() {
  const prompt = document.getElementById('prompt-text').value.trim();
  const limit = parseInt(document.getElementById('prompt-limit').value, 10);
  const discovery = document.getElementById('prompt-discovery').value;

  if (!prompt) {
    alert('Please enter a description or vibe prompt.');
    return;
  }

  const payload = {
    spotify_id: spotifyId,
    prompt: prompt,
    mode: 'prompt',
    limit: limit,
    discovery_preference: discovery
  };

  requestPlaylistCreation(payload);
}

function handleGuidedGeneration() {
  const genre = document.getElementById('guided-genre').value;
  const mood = document.getElementById('guided-mood').value;
  const activity = document.getElementById('guided-activity').value;
  const energy = document.getElementById('guided-energy').value;
  const language = document.getElementById('guided-language').value;
  const discovery = document.getElementById('guided-discovery').value;
  const limit = parseInt(document.getElementById('guided-limit').value, 10);
  const customPrompt = document.getElementById('guided-prompt').value.trim();

  // Guided Mode needs at least one criteria or prompt
  if (!genre && !mood && !activity && !energy && !customPrompt) {
    alert('Please select at least one filter attribute or describe a custom instruction.');
    return;
  }

  const payload = {
    spotify_id: spotifyId,
    prompt: customPrompt,
    mode: 'guided',
    limit: limit,
    genre: genre || null,
    mood: mood || null,
    activity: activity || null,
    energy_level: energy || null,
    language: language || null,
    discovery_preference: discovery
  };

  requestPlaylistCreation(payload);
}

function requestPlaylistCreation(payload) {
  startLoadingAnimation();
  showScreen('screen-loading');

  fetch(`${BACKEND_URL}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  .then(async (resp) => {
    stopLoadingAnimation();
    if (!resp.ok) {
      const errData = await resp.json();
      throw new Error(errData.detail || 'Failed to generate playlist.');
    }
    return resp.json();
  })
  .then(data => {
    displayResult(data);
  })
  .catch(err => {
    stopLoadingAnimation();
    alert(`Error: ${err.message}`);
    showScreen('screen-main');
  });
}

function handleRegenerateCurrent() {
  if (!currentPlaylistId || !spotifyId) return;

  startLoadingAnimation('Regenerating playlist tracks...');
  showScreen('screen-loading');

  fetch(`${BACKEND_URL}/api/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      spotify_id: spotifyId,
      playlist_id: currentPlaylistId
    })
  })
  .then(async (resp) => {
    stopLoadingAnimation();
    if (!resp.ok) {
      const errData = await resp.json();
      throw new Error(errData.detail || 'Failed to refresh tracks.');
    }
    return resp.json();
  })
  .then(data => {
    displayResult(data);
  })
  .catch(err => {
    stopLoadingAnimation();
    alert(`Regeneration Error: ${err.message}`);
    showScreen('screen-result'); // Return back to result screen on failure
  });
}

// ==========================================================================
// Curation Loader Text Animation Cycle
// ==========================================================================
function startLoadingAnimation(customMessage = null) {
  const statusEl = document.getElementById('loading-status');
  const messages = [
    'Analyzing your taste profile...',
    'Consulting with Google Gemini...',
    'Curating matching song recommendations...',
    'Searching tracks on Spotify API...',
    'Matching audio metadata and aesthetics...',
    'Building playlist shell on your account...',
    'Adding songs directly to Spotify...'
  ];

  if (customMessage) {
    statusEl.textContent = customMessage;
    return;
  }

  let idx = 0;
  statusEl.textContent = messages[idx];
  
  loadingInterval = setInterval(() => {
    idx = (idx + 1) % messages.length;
    statusEl.textContent = messages[idx];
  }, 3000);
}

function stopLoadingAnimation() {
  if (loadingInterval) {
    clearInterval(loadingInterval);
    loadingInterval = null;
  }
}

// ==========================================================================
// Result Display & Formatting
// ==========================================================================
function displayResult(data) {
  currentPlaylistId = data.playlist_id;

  document.getElementById('result-title').textContent = data.playlist_name;
  document.getElementById('result-description').textContent = data.playlist_description;
  document.getElementById('result-explanation').textContent = data.explanation;

  const trackContainer = document.getElementById('result-tracks');
  trackContainer.innerHTML = '';

  data.tracks.forEach(t => {
    const item = document.createElement('div');
    item.className = `track-item ${t.found ? '' : 'not-found'}`;

    const info = document.createElement('div');
    info.className = 'track-info';
    
    const name = document.createElement('span');
    name.className = 'track-name';
    name.textContent = t.title;

    const artist = document.createElement('span');
    artist.className = 'track-artist';
    artist.textContent = t.artist;

    info.appendChild(name);
    info.appendChild(artist);

    const status = document.createElement('span');
    status.className = `track-status ${t.found ? 'found' : 'not-found'}`;
    status.textContent = t.found ? '✓' : '✖';

    item.appendChild(info);
    item.appendChild(status);
    trackContainer.appendChild(item);
  });

  showScreen('screen-result');
}

// ==========================================================================
// Playlist History Handling
// ==========================================================================
function loadHistory() {
  if (!spotifyId) return;

  const emptyEl = document.getElementById('history-empty');
  const listEl = document.getElementById('history-list');

  fetch(`${BACKEND_URL}/api/history/${spotifyId}`)
    .then(resp => resp.json())
    .then(data => {
      if (data.success && data.playlists && data.playlists.length > 0) {
        emptyEl.style.display = 'none';
        listEl.style.display = 'flex';
        listEl.innerHTML = '';
        
        data.playlists.forEach(playlist => {
          const card = renderHistoryCard(playlist);
          listEl.appendChild(card);
        });
      } else {
        emptyEl.style.display = 'flex';
        listEl.style.display = 'none';
      }
    })
    .catch(err => {
      console.error('Failed to load playlist history:', err);
      emptyEl.style.display = 'flex';
      listEl.style.display = 'none';
    });
}

function renderHistoryCard(playlist) {
  const card = document.createElement('div');
  card.className = 'history-card';

  // Format date
  let dateStr = 'Recently';
  try {
    const date = new Date(playlist.created_at);
    dateStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch(e) {}

  // Card HTML structure
  card.innerHTML = `
    <div class="history-card-header">
      <div class="history-card-title">${playlist.name}</div>
      <span class="history-card-date">${dateStr}</span>
    </div>
    <div class="history-card-desc">${playlist.description || ''}</div>
    <div class="history-card-actions">
      <button class="btn btn-primary btn-history-open" data-url="https://open.spotify.com/playlist/${playlist.playlist_id}">Open</button>
      <button class="btn btn-secondary btn-history-refresh" data-id="${playlist.playlist_id}">Refresh</button>
    </div>
  `;

  // Attach actions
  card.querySelector('.btn-history-open').addEventListener('click', (e) => {
    const url = e.target.getAttribute('data-url');
    safeTabs.create({ url: url });
  });

  card.querySelector('.btn-history-refresh').addEventListener('click', (e) => {
    const pid = e.target.getAttribute('data-id');
    currentPlaylistId = pid;
    handleRegenerateCurrent();
  });

  return card;
}
