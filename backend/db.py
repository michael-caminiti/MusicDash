import os
import sqlite3

DB_PATH = os.getenv("MUSICDASH_DB", os.path.join(os.path.dirname(__file__), "dashboard.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS taste_profile_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    current_taste_signals TEXT,
    strong_genres_json TEXT,
    emerging_genres_json TEXT,
    defining_artists_json TEXT,
    things_to_avoid_json TEXT,
    playlist_ideas_json TEXT,
    next_field_trip_genre TEXT,
    monthly_tracking_notes TEXT,
    raw_body TEXT,
    schema_matched INTEGER NOT NULL DEFAULT 0,
    is_empty_template INTEGER NOT NULL DEFAULT 0,
    file_mtime TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT NOT NULL,
    source_generator TEXT NOT NULL CHECK (source_generator IN ('chatgpt', 'claude')),
    file_path TEXT NOT NULL UNIQUE,
    top_artists_json TEXT,
    top_tracks_json TEXT,
    is_empty_template INTEGER NOT NULL DEFAULT 0,
    file_mtime TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews(review_date);

CREATE TABLE IF NOT EXISTS scrobbles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL,
    track TEXT NOT NULL,
    album TEXT,
    played_at TEXT NOT NULL,
    url TEXT,
    source_file TEXT NOT NULL,
    UNIQUE(artist, track, played_at)
);
CREATE INDEX IF NOT EXISTS idx_scrobbles_played_at ON scrobbles(played_at);
CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_name TEXT NOT NULL,
    track_uri TEXT,
    track_name TEXT NOT NULL,
    artist_names TEXT NOT NULL,
    album_name TEXT,
    album_release_date TEXT,
    duration_ms INTEGER,
    popularity INTEGER,
    isrc TEXT,
    added_at TEXT,
    export_date TEXT NOT NULL,
    source_file TEXT NOT NULL,
    UNIQUE(playlist_name, track_uri, added_at)
);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks_playlist ON playlist_tracks(playlist_name);

CREATE TABLE IF NOT EXISTS purchase_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    spotify_album_id TEXT,
    image_url TEXT,
    discogs_release_id INTEGER,
    discogs_thumb_url TEXT,
    example_track TEXT,
    last_checked TEXT NOT NULL,
    UNIQUE(artist, album)
);

CREATE TABLE IF NOT EXISTS purchase_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    seed_artist TEXT NOT NULL,
    score REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artist_images (
    artist_name TEXT PRIMARY KEY,
    spotify_id TEXT,
    image_url TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_idea_playlists (
    idea_title TEXT PRIMARY KEY,
    playlist_id TEXT NOT NULL,
    playlist_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_idea_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_title TEXT NOT NULL,
    track_uri TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'text_match'
        CHECK (category IN ('seed_artist', 'similar_artist', 'genre_tag', 'text_match')),
    confidence REAL NOT NULL DEFAULT 0.4,
    status TEXT NOT NULL DEFAULT 'added' CHECK (status IN ('added', 'rejected')),
    added_at TEXT NOT NULL,
    UNIQUE(idea_title, track_uri)
);
CREATE INDEX IF NOT EXISTS idx_playlist_idea_tracks_idea ON playlist_idea_tracks(idea_title);

CREATE TABLE IF NOT EXISTS dismissed_playlist_ideas (
    idea_title TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generated_playlist_ideas (
    title TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS genre_primers (
    genre TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('wikipedia', 'llm')),
    history_text TEXT NOT NULL,
    source_url TEXT,
    key_artists_json TEXT NOT NULL,
    sonic_signatures_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_trip_playlists (
    genre_note TEXT PRIMARY KEY,
    idea_title TEXT NOT NULL,
    idea_description TEXT NOT NULL,
    playlist_id TEXT NOT NULL,
    playlist_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tour_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_date TEXT NOT NULL,
    venue TEXT,
    city TEXT,
    ticketmaster_url TEXT,
    ticketmaster_id TEXT UNIQUE,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'interested', 'going', 'passed')),
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tour_events_date ON tour_events(event_date);

CREATE TABLE IF NOT EXISTS playlist_liner_notes (
    idea_title TEXT PRIMARY KEY,
    liner_notes TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS album_palette (
    spotify_album_id TEXT PRIMARY KEY,
    dominant_color_hex TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discogs_collection_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_norm TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discogs_collection_cache_norm ON discogs_collection_cache(artist_norm, title_norm);

CREATE TABLE IF NOT EXISTS ingest_log (
    file_path TEXT PRIMARY KEY,
    file_mtime TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    rows_affected INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok'
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# (table, column, ddl) — CREATE TABLE IF NOT EXISTS in SCHEMA only covers brand-new installs;
# columns added to an already-existing table need an explicit ALTER TABLE migration.
COLUMN_MIGRATIONS = [
    ("playlist_idea_tracks", "category", "TEXT NOT NULL DEFAULT 'text_match'"),
    ("playlist_idea_tracks", "confidence", "REAL NOT NULL DEFAULT 0.4"),
    ("purchase_items", "bandcamp_url", "TEXT"),
    ("purchase_items", "bandcamp_item_id", "INTEGER"),
    ("purchase_items", "bandcamp_item_type", "TEXT"),
    ("purchase_items", "added_at", "TEXT"),
    ("tour_events", "preshow_playlist_id", "TEXT"),
    ("tour_events", "preshow_playlist_url", "TEXT"),
    ("tour_events", "source", "TEXT NOT NULL DEFAULT 'ticketmaster'"),
    ("tour_events", "songkick_uid", "TEXT"),
]


def _migrate_columns(conn: sqlite3.Connection) -> None:
    for table, column, ddl in COLUMN_MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS discogs_wantlist")
        conn.executescript(SCHEMA)
        _migrate_columns(conn)
        conn.commit()
    finally:
        conn.close()
