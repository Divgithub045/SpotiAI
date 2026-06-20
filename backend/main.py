import urllib.parse
import time
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

from backend import config
from backend import database
from backend import spotify_client
from backend import gemini_client
from backend.logger import logger

app = FastAPI(
    title="Spotify AI Playlist Creator API",
    description="Backend API for OAuth authentication, Gemini playlist curation, and Spotify integrations.",
    version="1.0.0"
)

# Allow CORS so that the Chrome extension can communicate with our API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Extensions make requests from chrome-extension:// origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom logging middleware to log incoming HTTP requests and their processing time
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    method = request.method
    path = request.url.path
    client_host = request.client.host if request.client else "unknown"
    
    logger.info(f"Incoming request: {method} {path} from {client_host}")
    
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        logger.info(f"Completed request: {method} {path} - Status: {response.status_code} - Duration: {process_time:.2f}ms")
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(f"Failed request: {method} {path} - Error: {str(e)} - Duration: {process_time:.2f}ms")
        raise

@app.on_event("startup")
def startup_db():
    """Ensure SQLite database and tables are created on API startup."""
    database.init_db()

# Models
class GenerateRequest(BaseModel):
    spotify_id: str
    prompt: Optional[str] = ""
    mode: str = "prompt"  # "prompt" or "guided"
    limit: int = 20
    genre: Optional[str] = None
    mood: Optional[str] = None
    activity: Optional[str] = None
    energy_level: Optional[str] = None
    language: Optional[str] = None
    discovery_preference: Optional[str] = "balanced"

class RegenerateRequest(BaseModel):
    spotify_id: str
    playlist_id: str

@app.get("/api/health")
def health_check():
    """Verify backend status."""
    return {"status": "ok", "database": config.DATABASE_NAME}

@app.get("/api/login")
def login(extension_id: str = Query(..., description="Chrome extension ID to redirect to post-login")):
    """Redirect user to Spotify login for authorization."""
    if not extension_id:
        raise HTTPException(status_code=400, detail="Missing extension_id parameter.")
    
    auth_url = spotify_client.get_auth_url(extension_id)
    return RedirectResponse(auth_url)

@app.get("/api/callback")
async def callback(code: Optional[str] = None, error: Optional[str] = None, state: Optional[str] = None):
    """Callback receiver for Spotify authentication."""
    if error or not code:
        logger.error(f"Spotify authentication error: {error}")
        return HTMLResponse(
            content="""
            <html>
                <body style="font-family: sans-serif; background-color: #121212; color: #ff5555; text-align: center; padding: 50px;">
                    <h1>Authentication Failed</h1>
                    <p>Error reported by Spotify: {}</p>
                    <p>You can close this tab and try again.</p>
                </body>
            </html>
            """.format(error or "Missing auth code"),
            status_code=400
        )
    
    # state parameter contains the extension_id passed during /api/login
    extension_id = state
    if not extension_id:
        return HTMLResponse(
            content="<h3>Error: Missing extension ID in state parameter.</h3>", 
            status_code=400
        )
        
    try:
        # Exchange authorization code for tokens
        tokens = await spotify_client.get_tokens_from_code(code)
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        expires_in = tokens["expires_in"]
        
        # Get Spotify user profile details
        profile = await spotify_client.get_user_profile(access_token)
        spotify_id = profile["id"]
        display_name = profile.get("display_name") or spotify_id
        email = profile.get("email") or ""
        
        # Save tokens and user details in SQLite database
        database.save_user(
            spotify_id=spotify_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            display_name=display_name,
            email=email
        )
        
        # Redirect the user's browser back to the extension page
        encoded_name = urllib.parse.quote(display_name)
        redirect_url = f"chrome-extension://{extension_id}/callback.html?spotify_id={spotify_id}&display_name={encoded_name}"
        return RedirectResponse(redirect_url)
        
    except Exception as e:
        logger.exception(f"Error handling callback authorization: {e}")
        return HTMLResponse(
            content=f"<h3>Authentication error: {str(e)}</h3>", 
            status_code=500
        )

@app.get("/api/user/{spotify_id}")
def get_user_status(spotify_id: str):
    """Check if the user is authenticated and return profile details."""
    user = database.get_user(spotify_id)
    if user:
        return {
            "logged_in": True,
            "spotify_id": user["spotify_id"],
            "display_name": user["display_name"],
            "email": user["email"]
        }
    return {"logged_in": False}


@app.post("/api/generate")
async def generate_playlist(req: GenerateRequest):
    """
    Generate an AI playlist matching user specifications, search for matching tracks,
    create a playlist on Spotify, populate it, and record it in database history.
    """
    try:
        # Get active access token (refreshes if expired)
        token = await spotify_client.get_active_token(req.spotify_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=401, detail="Spotify authorization failed.")

    # Retrieve user taste profile from Spotify (top artists/tracks)
    try:
        taste_profile = await spotify_client.get_user_taste_profile(token)
    except Exception as e:
        logger.warning(f"Could not retrieve taste profile: {e}")
        taste_profile = {"top_artists": [], "top_tracks": []}
        
    # Query Gemini for playlist details and track recommendation list
    try:
        curated_playlist = await gemini_client.generate_playlist_recommendations(
            prompt=req.prompt,
            mode=req.mode,
            taste_profile=taste_profile,
            limit=req.limit,
            genre=req.genre,
            mood=req.mood,
            activity=req.activity,
            energy_level=req.energy_level,
            language=req.language,
            discovery_preference=req.discovery_preference
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini curation failed: {str(e)}")

    # Search Spotify for the recommended tracks and aggregate URIs
    track_uris = []
    tracks_meta = []
    
    for track in curated_playlist.get("tracks", []):
        title = track.get("title", "")
        artist = track.get("artist", "")
        
        if not title or not artist:
            continue
            
        uri = await spotify_client.search_track(token, title, artist)
        if uri:
            track_uris.append(uri)
            tracks_meta.append({"title": title, "artist": artist, "uri": uri, "found": True})
        else:
            tracks_meta.append({"title": title, "artist": artist, "uri": None, "found": False})
            
    if not track_uris:
        raise HTTPException(
            status_code=422, 
            detail="Could not match any of the AI recommended tracks on Spotify. Try refinement or different inputs."
        )

    # Create the playlist on Spotify
    try:
        playlist_name = curated_playlist.get("playlist_name", "AI Curated Playlist")
        playlist_desc = curated_playlist.get("playlist_description", "Generated with Spotify AI Playlist Creator.")
        
        playlist_id = await spotify_client.create_playlist(
            access_token=token,
            spotify_id=req.spotify_id,
            name=playlist_name,
            description=playlist_desc
        )
        
        # Populate the playlist with tracks
        await spotify_client.add_tracks_to_playlist(token, playlist_id, track_uris)
        
        # Save playlist metadata in SQLite history
        database.save_playlist(
            spotify_id=req.spotify_id,
            playlist_id=playlist_id,
            name=playlist_name,
            description=playlist_desc,
            prompt=req.prompt or f"Genre: {req.genre or 'Mix'}, Mood: {req.mood or 'Mix'}",
            mode=req.mode,
            explanation=curated_playlist.get("explanation", ""),
            tracks=tracks_meta
        )
        
        return {
            "success": True,
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "playlist_description": playlist_desc,
            "explanation": curated_playlist.get("explanation", ""),
            "spotify_url": f"https://open.spotify.com/playlist/{playlist_id}",
            "tracks": tracks_meta
        }
        
    except Exception as e:
        logger.exception(f"Error building Spotify playlist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create playlist on Spotify: {str(e)}")

@app.get("/api/history/{spotify_id}")
def get_history(spotify_id: str):
    """Retrieve all playlists generated by this app for the user."""
    try:
        playlists = database.get_user_playlists(spotify_id)
        return {"success": True, "playlists": playlists}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")

@app.post("/api/regenerate")
async def regenerate_playlist(req: RegenerateRequest):
    """
    Refresh an existing playlist with a brand new selection of songs, 
    overwriting the Spotify playlist tracklist and history record.
    """
    # 1. Fetch playlist from database
    db_playlist = database.get_playlist(req.playlist_id)
    if not db_playlist:
        raise HTTPException(status_code=404, detail="Playlist history record not found.")
        
    try:
        # Get active access token
        token = await spotify_client.get_active_token(req.spotify_id)
    except Exception as e:
        raise HTTPException(status_code=401, detail="Authentication failed.")
        
    # 2. Get user taste profile
    try:
        taste_profile = await spotify_client.get_user_taste_profile(token)
    except Exception as e:
        taste_profile = {"top_artists": [], "top_tracks": []}
        
    # 3. Create the refresh prompt for Gemini (specifying exclusions)
    current_tracks_str = "\n".join([f"- {t['title']} by {t['artist']}" for t in db_playlist["tracks"]])
    refresh_prompt = f"""
I want to refresh/regenerate the playlist "{db_playlist['name']}".
Theme / Original prompt: "{db_playlist['prompt']}"

CURRENT SONGS (DO NOT RECOMMEND ANY OF THESE SONGS):
{current_tracks_str}

Please generate a completely fresh set of songs (exactly {len(db_playlist["tracks"]) or 20} songs) that fits the exact same mood and style. 
Return the output using the same schema, and feel free to adjust the description slightly to note it has been refreshed.
"""

    try:
        # Request new playlist recommendation
        curated_playlist = await gemini_client.generate_playlist_recommendations(
            prompt=refresh_prompt,
            mode="prompt",
            taste_profile=taste_profile,
            limit=len(db_playlist["tracks"]) or 20,
            discovery_preference="balanced"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini curation refresh failed: {str(e)}")

    # 4. Search Spotify for new tracks
    track_uris = []
    tracks_meta = []
    
    for track in curated_playlist.get("tracks", []):
        title = track.get("title", "")
        artist = track.get("artist", "")
        
        if not title or not artist:
            continue
            
        uri = await spotify_client.search_track(token, title, artist)
        if uri:
            track_uris.append(uri)
            tracks_meta.append({"title": title, "artist": artist, "uri": uri, "found": True})
        else:
            tracks_meta.append({"title": title, "artist": artist, "uri": None, "found": False})
            
    if not track_uris:
         raise HTTPException(status_code=422, detail="Could not match any of the fresh AI recommended songs on Spotify.")

    # 5. Replace tracks in the Spotify playlist and update database
    try:
        playlist_name = curated_playlist.get("playlist_name", db_playlist["name"])
        playlist_desc = curated_playlist.get("playlist_description", db_playlist["description"])
        
        # Call Spotify replace API
        await spotify_client.replace_playlist_tracks(token, req.playlist_id, track_uris)
        
        # Update SQLite record
        database.update_playlist_history(
            playlist_id=req.playlist_id,
            new_name=playlist_name,
            new_description=playlist_desc,
            new_tracks=tracks_meta
        )
        
        return {
            "success": True,
            "playlist_id": req.playlist_id,
            "playlist_name": playlist_name,
            "playlist_description": playlist_desc,
            "explanation": curated_playlist.get("explanation", ""),
            "spotify_url": f"https://open.spotify.com/playlist/{req.playlist_id}",
            "tracks": tracks_meta
        }
    except Exception as e:
        logger.exception(f"Error regenerating Spotify playlist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to replace tracks in Spotify playlist: {str(e)}")
