import sqlite3
import json
import time
import datetime
from typing import Dict, Any, List, Optional
from backend.config import DATABASE_NAME
from backend.logger import logger

def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables for users and playlists."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            spotify_id TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            expires_at REAL NOT NULL,
            display_name TEXT,
            email TEXT
        )
    ''')
    
    # Create playlists table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_id TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            prompt TEXT,
            mode TEXT,
            explanation TEXT,
            created_at TEXT NOT NULL,
            tracks_json TEXT NOT NULL,
            FOREIGN KEY (spotify_id) REFERENCES users (spotify_id)
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

def save_user(spotify_id: str, access_token: str, refresh_token: str, expires_in: int, display_name: str, email: str):
    """Save or update user tokens and details."""
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = time.time() + expires_in
    
    cursor.execute('''
        INSERT INTO users (spotify_id, access_token, refresh_token, expires_at, display_name, email)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            display_name=excluded.display_name,
            email=excluded.email
    ''', (spotify_id, access_token, refresh_token, expires_at, display_name, email))
    conn.commit()
    conn.close()

def get_user(spotify_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve user details by Spotify ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE spotify_id = ?', (spotify_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_latest_user() -> Optional[Dict[str, Any]]:
    """Retrieve the most recently authenticated user (by highest rowid).
    Used by the extension popup poll loop to detect login completion."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users ORDER BY rowid DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def update_user_tokens(spotify_id: str, access_token: str, expires_in: int):
    """Update access token and its expiration time for an existing user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    expires_at = time.time() + expires_in
    
    cursor.execute('''
        UPDATE users 
        SET access_token = ?, expires_at = ?
        WHERE spotify_id = ?
    ''', (access_token, expires_at, spotify_id))
    conn.commit()
    conn.close()

def save_playlist(spotify_id: str, playlist_id: str, name: str, description: str, prompt: str, mode: str, explanation: str, tracks: List[Dict[str, Any]]):
    """Save a newly created playlist and its tracklist to history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    created_at = datetime.datetime.utcnow().isoformat()
    tracks_json = json.dumps(tracks)
    
    cursor.execute('''
        INSERT INTO playlists (spotify_id, playlist_id, name, description, prompt, mode, explanation, created_at, tracks_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (spotify_id, playlist_id, name, description, prompt, mode, explanation, created_at, tracks_json))
    conn.commit()
    conn.close()

def get_user_playlists(spotify_id: str) -> List[Dict[str, Any]]:
    """Retrieve all playlists generated for a specific user, ordered by newest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM playlists WHERE spotify_id = ? ORDER BY id DESC', (spotify_id,))
    rows = cursor.fetchall()
    conn.close()
    
    playlists = []
    for r in rows:
        p = dict(r)
        p['tracks'] = json.loads(p['tracks_json'])
        playlists.append(p)
    return playlists

def get_playlist(playlist_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve details of a single playlist by its Spotify playlist ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM playlists WHERE playlist_id = ?', (playlist_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        p = dict(row)
        p['tracks'] = json.loads(p['tracks_json'])
        return p
    return None

def update_playlist_history(playlist_id: str, new_name: str, new_description: str, new_tracks: List[Dict[str, Any]]):
    """Update the tracks and details of a playlist in history after regeneration."""
    conn = get_db_connection()
    cursor = conn.cursor()
    tracks_json = json.dumps(new_tracks)
    cursor.execute('''
        UPDATE playlists 
        SET name = ?, description = ?, tracks_json = ?
        WHERE playlist_id = ?
    ''', (new_name, new_description, tracks_json, playlist_id))
    conn.commit()
    conn.close()
