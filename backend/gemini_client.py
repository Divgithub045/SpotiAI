import json
import httpx
from typing import Dict, Any, List
from backend import config
from backend.logger import logger

# Fallback chain of Gemini models to use if the primary or high-demand models are rate-limited or out of quota
MODELS_TO_TRY = [
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash-lite",
    "gemini-flash-lite-latest"
]

async def generate_playlist_recommendations(
    prompt: str,
    mode: str,
    taste_profile: Dict[str, Any],
    limit: int = 20,
    genre: str = None,
    mood: str = None,
    activity: str = None,
    energy_level: str = None,
    language: str = None,
    discovery_preference: str = "balanced"
) -> Dict[str, Any]:
    """
    Sends the user's preferences, combined with their Spotify taste profile context, 
    to Gemini and returns structured song recommendations.
    """
    if not config.GEMINI_API_KEY:
        raise ValueError("Gemini API Key is missing. Please configure it in the .env file.")

    # Format the user's top Spotify tracks and artists as prompt context
    top_artists = taste_profile.get("top_artists", [])
    top_tracks = taste_profile.get("top_tracks", [])
    
    taste_context = ""
    if top_artists or top_tracks:
        artists_str = ", ".join(top_artists)
        tracks_str = "\n".join([f"- {t}" for t in top_tracks])
        taste_context = f"""
USER SPOTIFY LISTENING HISTORY (TASTE PROFILE):
- Top Artists: {artists_str}
- Top Tracks:
{tracks_str}

Please use this history context to personalize the recommended songs to match their general preferences (e.g. subgenres, vocals, production style). Do not just copy/suggest the same tracks they already listen to; rather, suggest similar or related tracks that fit the prompt perfectly.
"""

    # Assemble request based on mode
    if mode == "guided":
        request_details = f"""
PLAYLIST PREFERENCES (GUIDED MODE):
- Target Genre: {genre or 'Any/Mix'}
- Mood: {mood or 'Any'}
- Activity/Setting: {activity or 'Any'}
- Energy Level: {energy_level or 'Balanced'}
- Lyrics Language: {language or 'Any'}
- Discovery Preference: {discovery_preference or 'Balanced'} (popular hits vs hidden gems vs balanced)
- User Prompt Instructions: "{prompt or 'None'}"
- Number of tracks to recommend: {limit}
"""
    else:
        request_details = f"""
PLAYLIST PREFERENCES (PROMPT MODE):
- Natural Language Prompt: "{prompt}"
- Discovery Preference: {discovery_preference or 'Balanced'}
- Number of tracks to recommend: {limit}
"""

    system_instruction = f"""
You are an expert AI music curator. Your task is to recommend a list of real, existing tracks on Spotify matching the user's preferences.
{taste_context}
{request_details}

Curation Guidelines:
1. Generate EXACTLY {limit} tracks. Do not generate duplicates.
2. For discovery preference:
   - "popular": Recommend widely known, mainstream hits.
   - "hidden_gems": Recommend indie, underground, or lesser-known tracks from smaller artists.
   - "balanced": Recommend a healthy mix of both.
3. Suggest a creative, catchy title and a vibrant, personalized description.
4. Write a concise, 2-3 sentence explanation explaining the vibe and why these tracks were selected for the user.
5. Make sure the song titles and artist names are spelled correctly so they can be matched on Spotify.
"""

    # Payload matching Gemini Structured Output schema
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": system_instruction}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "playlist_name": {"type": "STRING"},
                    "playlist_description": {"type": "STRING"},
                    "explanation": {"type": "STRING"},
                    "tracks": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING"},
                                "artist": {"type": "STRING"}
                            },
                            "required": ["title", "artist"]
                        }
                    }
                },
                "required": ["playlist_name", "playlist_description", "explanation", "tracks"]
            }
        }
    }

    last_error_message = ""
    async with httpx.AsyncClient() as client:
        for model in MODELS_TO_TRY:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
            try:
                logger.info(f"Attempting to generate playlist recommendations using model: {model}...")
                resp = await client.post(url, json=payload, timeout=60.0)
                
                if resp.status_code == 200:
                    # Extract JSON string from parts
                    result_data = resp.json()
                    raw_text = result_data["candidates"][0]["content"]["parts"][0]["text"]
                    # Parse and return JSON
                    return json.loads(raw_text)
                
                # Handle non-200 responses
                error_body = resp.text
                logger.warning(f"Model {model} failed with status {resp.status_code}. Response: {error_body}")
                last_error_message = f"Model {model} ({resp.status_code}): {error_body}"
                
            except Exception as e:
                logger.exception(f"Exception occurred while calling model {model}: {e}")
                last_error_message = f"Model {model} (exception): {str(e)}"
                
        # If all models fail, raise a detailed exception
        logger.error(f"All Gemini models in the fallback chain failed. Last error: {last_error_message}")
        raise Exception(f"Google Gemini API error: All models in the fallback chain were exhausted or rate-limited. Last error details: {last_error_message}")
