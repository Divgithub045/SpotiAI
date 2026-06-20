import base64
import time
import httpx
from typing import Dict, Any, List, Optional
from backend import config
from backend import database
from backend.logger import logger

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"

def get_auth_url(extension_id: str) -> str:
    """Generate the Spotify login URL with required scopes and extension ID in the state."""
    scopes = "user-read-private user-read-email user-top-read playlist-modify-public playlist-modify-private"
    params = {
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "scope": scopes,
        "state": extension_id,
        "show_dialog": "true"  # Force display of the login dialog
    }
    # Create request to easily format params into query string
    req = httpx.Request("GET", SPOTIFY_AUTH_URL, params=params)
    return str(req.url)

async def get_tokens_from_code(code: str) -> Dict[str, Any]:
    """Exchange Spotify authorization code for access and refresh tokens."""
    auth_bytes = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode("utf-8")
    auth_header = base64.b64encode(auth_bytes).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_REDIRECT_URI
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(SPOTIFY_TOKEN_URL, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    """Refresh an expired Spotify access token using the refresh token."""
    auth_bytes = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode("utf-8")
    auth_header = base64.b64encode(auth_bytes).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(SPOTIFY_TOKEN_URL, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

async def get_user_profile(access_token: str) -> Dict[str, Any]:
    """Retrieve user details (id, display name, email) from Spotify."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SPOTIFY_API_BASE_URL}/me", headers=headers)
        resp.raise_for_status()
        return resp.json()

async def get_active_token(spotify_id: str) -> str:
    """Retrieve a valid access token for the user, refreshing it if expired."""
    user = database.get_user(spotify_id)
    if not user:
        raise ValueError(f"User {spotify_id} not found in database.")
    
    # Refresh if token has expired or is expiring in less than 60 seconds
    if time.time() + 60 >= user['expires_at']:
        logger.info(f"Token expired for user {spotify_id}. Refreshing...")
        try:
            tokens = await refresh_access_token(user['refresh_token'])
            access_token = tokens['access_token']
            expires_in = tokens['expires_in']
            # Update database
            database.update_user_tokens(spotify_id, access_token, expires_in)
            return access_token
        except Exception as e:
            logger.exception(f"Failed to refresh token for user {spotify_id}: {e}")
            raise e
            
    return user['access_token']

async def get_user_taste_profile(access_token: str) -> Dict[str, Any]:
    """Retrieve top artists and top tracks to build a user taste context."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        # Retrieve user's top artists
        artists_resp = await client.get(
            f"{SPOTIFY_API_BASE_URL}/me/top/artists?limit=15&time_range=medium_term", 
            headers=headers
        )
        # Retrieve user's top tracks
        tracks_resp = await client.get(
            f"{SPOTIFY_API_BASE_URL}/me/top/tracks?limit=15&time_range=medium_term", 
            headers=headers
        )
        
        top_artists = []
        if artists_resp.status_code == 200:
            top_artists = [artist['name'] for artist in artists_resp.json().get('items', [])]
            
        top_tracks = []
        if tracks_resp.status_code == 200:
            for track in tracks_resp.json().get('items', []):
                track_name = track['name']
                artist_names = ", ".join([artist['name'] for artist in track['artists']])
                top_tracks.append(f"{track_name} by {artist_names}")
                
        return {"top_artists": top_artists, "top_tracks": top_tracks}

async def search_track(access_token: str, title: str, artist: str) -> Optional[str]:
    """Search Spotify for a track matching the title and artist, returning the URI."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        # 1. Search strictly with track and artist keywords
        query = f'track:"{title}" artist:"{artist}"'
        params = {"q": query, "type": "track", "limit": 1}
        try:
            resp = await client.get(f"{SPOTIFY_API_BASE_URL}/search", params=params, headers=headers)
            if resp.status_code == 200:
                items = resp.json().get("tracks", {}).get("items", [])
                if items:
                    return items[0]["uri"]
        except Exception as e:
            logger.warning(f"Error doing strict search for '{title}' by '{artist}': {e}")
            
        # 2. Fallback search: broader query with no strict quotes
        query_fallback = f"{title} {artist}"
        params_fallback = {"q": query_fallback, "type": "track", "limit": 1}
        try:
            resp_fb = await client.get(f"{SPOTIFY_API_BASE_URL}/search", params=params_fallback, headers=headers)
            if resp_fb.status_code == 200:
                items_fb = resp_fb.json().get("tracks", {}).get("items", [])
                if items_fb:
                    return items_fb[0]["uri"]
        except Exception as e:
            logger.warning(f"Error doing fallback search for '{title} {artist}': {e}")
            
        return None

async def create_playlist(access_token: str, spotify_id: str, name: str, description: str) -> str:
    """Create a new playlist in the user's Spotify account and return its ID."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {
        "name": name,
        "description": description,
        "public": False
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SPOTIFY_API_BASE_URL}/me/playlists", 
                json=data, 
                headers=headers
            )
            resp.raise_for_status()
            return resp.json()["id"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                raise Exception(
                    "Spotify returned 403 Forbidden. This typically means either:\n"
                    "1. Your login session does not have playlist-modify permissions (try logging out of the extension and logging back in).\n"
                    "2. Your Spotify account is not added to the 'Users and Access' list in your Spotify Developer Dashboard."
                )
            raise e

async def add_tracks_to_playlist(access_token: str, playlist_id: str, track_uris: List[str]) -> Dict[str, Any]:
    """Add a list of track URIs to a Spotify playlist."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    # Spotify allows maximum 100 tracks per request
    data = {"uris": track_uris[:100]}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SPOTIFY_API_BASE_URL}/playlists/{playlist_id}/items", 
                json=data, 
                headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                raise Exception(
                    "Spotify returned 403 Forbidden while adding tracks. This typically means either:\n"
                    "1. Your login session lacks playlist-modify permissions (try logging out and back in).\n"
                    "2. Your Spotify account is not added to 'Users and Access' in your Spotify Developer Dashboard."
                )
            raise e

async def replace_playlist_tracks(access_token: str, playlist_id: str, track_uris: List[str]) -> Dict[str, Any]:
    """Replace all tracks in a Spotify playlist with new track URIs."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {"uris": track_uris[:100]}
    async with httpx.AsyncClient() as client:
        try:
            # PUT method replaces all tracks in the playlist
            resp = await client.put(
                f"{SPOTIFY_API_BASE_URL}/playlists/{playlist_id}/items", 
                json=data, 
                headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                raise Exception(
                    "Spotify returned 403 Forbidden while replacing tracks. This typically means either:\n"
                    "1. Your login session lacks playlist-modify permissions (try logging out and back in).\n"
                    "2. Your Spotify account is not added to 'Users and Access' in your Spotify Developer Dashboard."
                )
            raise e
