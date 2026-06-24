import colorsys
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import db, ingest
from .connectors.bandcamp import BandcampConnector
from .connectors.discogs import DiscogsConnector
from .connectors.groq import GroqConnector
from .connectors.lastfm import LastfmConnector
from .connectors.llm import LLMConnector
from .connectors.setlistfm import SetlistFmConnector
from .connectors.songkick import SongkickConnector
from .connectors.spotify import SpotifyConnector
from .connectors.ticketmaster import TicketmasterConnector
from .connectors.wikipedia import WikipediaConnector
from .markdown_render import render_review_html

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

app = FastAPI(title="MusicDash")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

ARTIST_IMAGE_TTL_DAYS = 30
FINDS_PLAYLIST_ID = "7EZvHXHm8436XXk3mjJgpe"  # "2026 Finds"
RECOMMENDATION_SCORE_THRESHOLD = 0.15
MAX_RECOMMENDATIONS = 8
ASK_SCROBBLE_LIMIT = 5000

# Existing discovery playlists to dedup new playlist-idea tracks against, so "Create on Spotify"
# doesn't recreate something already sitting in one of these.
DISCOVERY_PLAYLIST_NAMES = {
    "2026 Finds", "Repeat Candidates", "Studio Inspiration",
    "Genre Field Trips", "Interesting, Not Sure Yet",
}
# Seed artists are bands the user already knows (they're the ones who named them in the idea) — they're
# anchors for taste, not the discovery target. Kept to 1 track each so they can't crowd out the
# similar-artist expansion below within the shared max_tracks budget (see the Emo Revival/Shoegaze
# Bridge bug: 6 seed artists x the old limit of 4 alone exceeded max_tracks, so every similar-artist
# candidate got truncated away and the "discovery" playlist was just more of the same named bands).
ARTIST_SEED_TRACK_LIMIT = 1
SIMILAR_ARTIST_EXPANSION = 4
SIMILAR_ARTIST_TRACK_LIMIT = 2
GENRE_TERM_TRACK_LIMIT = 4

# Strategy auto-disable: a category needs at least this many logged outcomes before its rejection
# rate is trusted enough to act on, and gets skipped entirely in future searches once its rejection
# rate crosses the threshold. seed_artist is exempt — it's the most reliable category by construction
# (exact name match) and disabling it would gut the feature on what's likely early sampling noise.
STRATEGY_MIN_SAMPLE_SIZE = 5
STRATEGY_REJECTION_THRESHOLD = 0.5
STRATEGY_EXEMPT_CATEGORIES = {"seed_artist"}


def _get_spotify_connector() -> SpotifyConnector:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI")
    if not (client_id and client_secret and redirect_uri):
        raise HTTPException(
            status_code=400,
            detail="SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REDIRECT_URI not configured in .env",
        )
    return SpotifyConnector(client_id, client_secret, redirect_uri)


def _get_discogs_connector() -> DiscogsConnector:
    token = os.getenv("DISCOGS_PERSONAL_ACCESS_TOKEN")
    username = os.getenv("DISCOGS_USERNAME")
    if not (token and username):
        raise HTTPException(
            status_code=400,
            detail="DISCOGS_PERSONAL_ACCESS_TOKEN / DISCOGS_USERNAME not configured in .env",
        )
    return DiscogsConnector(token, username)


def _get_ticketmaster_connector() -> TicketmasterConnector:
    api_key = os.getenv("TICKETMASTER_API_KEY")
    location = os.getenv("TICKETMASTER_LOCATION")
    if not (api_key and location):
        raise HTTPException(
            status_code=400,
            detail="TICKETMASTER_API_KEY / TICKETMASTER_LOCATION not configured in .env",
        )
    return TicketmasterConnector(api_key, location)


def _get_setlistfm_connector() -> SetlistFmConnector:
    api_key = os.getenv("SETLISTFM_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="SETLISTFM_API_KEY not configured in .env")
    return SetlistFmConnector(api_key)


def _get_songkick_connector() -> SongkickConnector:
    username = os.getenv("SONGKICK_USERNAME")
    if not username:
        raise HTTPException(status_code=400, detail="SONGKICK_USERNAME not configured in .env")
    return SongkickConnector(username)


def _get_lastfm_connector() -> LastfmConnector:
    api_key = os.getenv("LASTFM_API_KEY")
    user = os.getenv("LASTFM_USER")
    if not (api_key and user):
        raise HTTPException(status_code=400, detail="LASTFM_API_KEY / LASTFM_USER not configured in .env")
    return LastfmConnector(api_key, user)


def _get_llm_connector() -> LLMConnector:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY not configured in .env")
    return LLMConnector(api_key)


def _get_groq_connector() -> GroqConnector:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="GROQ_API_KEY not configured in .env")
    return GroqConnector(api_key)


@app.on_event("startup")
def on_startup():
    db.init_db()
    ingest.run_ingest()


def _row_to_taste_profile(row) -> dict:
    return {
        "snapshot_date": row["snapshot_date"],
        "current_taste_signals": row["current_taste_signals"],
        "strong_genres": json.loads(row["strong_genres_json"] or "[]"),
        "emerging_genres": json.loads(row["emerging_genres_json"] or "[]"),
        "defining_artists": json.loads(row["defining_artists_json"] or "[]"),
        "things_to_avoid": json.loads(row["things_to_avoid_json"] or "[]"),
        "playlist_ideas": json.loads(row["playlist_ideas_json"] or "[]"),
        "next_field_trip_genre": row["next_field_trip_genre"],
        "monthly_tracking_notes": row["monthly_tracking_notes"],
        "schema_matched": bool(row["schema_matched"]),
        "is_empty_template": bool(row["is_empty_template"]),
    }


@app.get("/api/taste-profile/current")
def get_taste_profile_current():
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM taste_profile_snapshots WHERE snapshot_date = 'CURRENT'"
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No current taste profile ingested yet.")
        profile = _row_to_taste_profile(row)
        dismissed = {
            r["idea_title"] for r in conn.execute("SELECT idea_title FROM dismissed_playlist_ideas").fetchall()
        }
        generated = conn.execute(
            "SELECT title, description FROM generated_playlist_ideas ORDER BY generated_at"
        ).fetchall()
        all_ideas = profile["playlist_ideas"] + [dict(g) for g in generated]
        profile["playlist_ideas"] = [i for i in all_ideas if i["title"] not in dismissed]

        profile["next_field_trip_playlist"] = None
        if profile["next_field_trip_genre"]:
            trip_row = conn.execute(
                "SELECT playlist_url FROM field_trip_playlists WHERE genre_note = ?",
                (profile["next_field_trip_genre"],),
            ).fetchone()
            if trip_row:
                profile["next_field_trip_playlist"] = {"playlist_url": trip_row["playlist_url"]}

        return profile
    finally:
        conn.close()


def _taste_context_text(profile: dict) -> str:
    return (
        f"Current taste signals: {profile['current_taste_signals'] or 'none'}\n"
        f"Strong genres: {', '.join(profile['strong_genres']) or 'none'}\n"
        f"Emerging genres: {', '.join(profile['emerging_genres']) or 'none'}\n"
        f"Defining artists: {', '.join(profile['defining_artists']) or 'none'}\n"
        f"Things to avoid: {', '.join(profile['things_to_avoid']) or 'none'}"
    )


GENRE_PRIMER_TTL_DAYS = 30


@app.get("/api/genre-primer")
def get_genre_primer(genre: str):
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM genre_primers WHERE genre = ?", (genre,)).fetchone()
        if row and (datetime.now(timezone.utc) - datetime.fromisoformat(row["generated_at"])).days < GENRE_PRIMER_TTL_DAYS:
            return {
                "genre": genre, "source": row["source"], "history_text": row["history_text"],
                "source_url": row["source_url"], "key_artists": json.loads(row["key_artists_json"]),
                "sonic_signatures": json.loads(row["sonic_signatures_json"]),
            }

        profile_row = conn.execute(
            "SELECT defining_artists_json FROM taste_profile_snapshots WHERE snapshot_date = 'CURRENT'"
        ).fetchone()
        defining_artists = json.loads(profile_row["defining_artists_json"] or "[]") if profile_row else []

        llm = _get_llm_connector()
        wiki = WikipediaConnector()
        summary = wiki.get_genre_summary(genre)

        try:
            if summary and summary["extract"]:
                primer = llm.get_genre_primer_from_wiki(genre, summary["extract"], defining_artists)
                source, history_text, source_url = "wikipedia", summary["extract"], summary["url"]
            else:
                primer = llm.get_genre_primer_without_wiki(genre, defining_artists)
                source, history_text, source_url = "llm", primer["history_text"], None
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to build genre primer: {e}")

        key_artists = primer.get("key_artists", [])
        sonic_signatures = primer.get("sonic_signatures", [])
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO genre_primers
                (genre, source, history_text, source_url, key_artists_json, sonic_signatures_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(genre) DO UPDATE SET
                source=excluded.source, history_text=excluded.history_text, source_url=excluded.source_url,
                key_artists_json=excluded.key_artists_json, sonic_signatures_json=excluded.sonic_signatures_json,
                generated_at=excluded.generated_at
            """,
            (genre, source, history_text, source_url, json.dumps(key_artists), json.dumps(sonic_signatures), now),
        )
        conn.commit()

        return {
            "genre": genre, "source": source, "history_text": history_text, "source_url": source_url,
            "key_artists": key_artists, "sonic_signatures": sonic_signatures,
        }
    finally:
        conn.close()


@app.post("/api/genre-primer/{genre}/starter-pack")
def post_genre_primer_starter_pack(genre: str):
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT key_artists_json FROM genre_primers WHERE genre = ?", (genre,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="No cached primer for this genre — load it with 'Tell Me More' first.")

    key_artists = json.loads(row["key_artists_json"] or "[]")
    if not key_artists:
        raise HTTPException(status_code=422, detail="This primer has no key artists to seed a playlist from.")

    title = f"{genre} Starter Pack"
    description = ", ".join(key_artists)
    return post_spotify_playlist_from_idea(PlaylistIdeaRequest(title=title, description=description))


@app.post("/api/playlist-ideas/generate")
def post_generate_playlist_idea():
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM taste_profile_snapshots WHERE snapshot_date = 'CURRENT'"
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No current taste profile ingested yet.")
        profile = _row_to_taste_profile(row)

        existing_titles = [i["title"] for i in profile["playlist_ideas"]]
        existing_titles += [
            r["title"] for r in conn.execute("SELECT title FROM generated_playlist_ideas").fetchall()
        ]
        existing_titles += [
            r["idea_title"] for r in conn.execute("SELECT idea_title FROM dismissed_playlist_ideas").fetchall()
        ]

        llm = _get_llm_connector()
        try:
            idea = llm.generate_playlist_idea(_taste_context_text(profile), existing_titles)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to generate a new idea: {e}")

        if not idea.get("title") or not idea.get("description"):
            raise HTTPException(status_code=502, detail="Generated idea was missing a title or description.")

        conn.execute(
            "INSERT INTO generated_playlist_ideas (title, description, generated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(title) DO UPDATE SET description=excluded.description, generated_at=excluded.generated_at",
            (idea["title"], idea["description"], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"title": idea["title"], "description": idea["description"]}
    finally:
        conn.close()


class IdeaTitleRequest(BaseModel):
    title: str


@app.get("/api/playlist-ideas/dismissed")
def get_dismissed_playlist_ideas():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT idea_title, dismissed_at FROM dismissed_playlist_ideas ORDER BY dismissed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/playlist-ideas/dismiss")
def post_dismiss_playlist_idea(req: IdeaTitleRequest):
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO dismissed_playlist_ideas (idea_title, dismissed_at) VALUES (?, ?) "
            "ON CONFLICT(idea_title) DO NOTHING",
            (req.title, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"dismissed": True}


@app.post("/api/playlist-ideas/restore")
def post_restore_playlist_idea(req: IdeaTitleRequest):
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM dismissed_playlist_ideas WHERE idea_title = ?", (req.title,))
        conn.commit()
    finally:
        conn.close()
    return {"restored": True}


@app.get("/api/taste-profile/history")
def get_taste_profile_history():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM taste_profile_snapshots WHERE snapshot_date != 'CURRENT' ORDER BY snapshot_date"
        ).fetchall()
        return [_row_to_taste_profile(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/genres/trend")
def get_genres_trend():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM taste_profile_snapshots "
            "WHERE snapshot_date != 'CURRENT' AND schema_matched = 1 AND is_empty_template = 0 "
            "ORDER BY snapshot_date"
        ).fetchall()
        dates = []
        genre_series = {}
        snapshots = []
        for row in rows:
            dates.append(row["snapshot_date"])
            genres = set(json.loads(row["strong_genres_json"] or "[]")) | set(
                json.loads(row["emerging_genres_json"] or "[]")
            )
            snapshots.append(genres)
        all_genres = sorted(set().union(*snapshots)) if snapshots else []
        for genre in all_genres:
            genre_series[genre] = [1 if genre in snap else 0 for snap in snapshots]
        return {"dates": dates, "genres": genre_series}
    finally:
        conn.close()


@app.get("/api/reviews")
def get_reviews():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE is_empty_template = 0 ORDER BY review_date DESC"
        ).fetchall()
        result = []
        for row in rows:
            top_artists = json.loads(row["top_artists_json"] or "[]")
            top_tracks = json.loads(row["top_tracks_json"] or "[]")
            result.append({
                "review_date": row["review_date"],
                "source_generator": row["source_generator"],
                "file_path": row["file_path"],
                "is_empty_template": bool(row["is_empty_template"]),
                "has_data": bool(top_artists or top_tracks),
            })
        return result
    finally:
        conn.close()


@app.get("/api/reviews/{review_date}/html", response_class=HTMLResponse)
def get_review_html(review_date: str):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT file_path FROM reviews WHERE review_date = ? ORDER BY file_mtime DESC LIMIT 1",
            (review_date,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No review found for {review_date}.")
        return render_review_html(row["file_path"])
    finally:
        conn.close()


@app.get("/api/scrobbles/top-artists")
def get_scrobbles_top_artists(period: str = "all", limit: int = 15):
    conn = db.get_connection()
    try:
        where = ""
        if period == "7day":
            where = "WHERE played_at >= datetime('now', '-7 days')"
        elif period == "30day":
            where = "WHERE played_at >= datetime('now', '-30 days')"
        rows = conn.execute(
            f"SELECT artist, COUNT(*) as plays FROM scrobbles {where} "
            "GROUP BY artist ORDER BY plays DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"artist": r["artist"], "plays": r["plays"]} for r in rows]
    finally:
        conn.close()


@app.post("/api/scrobbles/sync-live")
def post_scrobbles_sync_live():
    lastfm = _get_lastfm_connector()
    conn = db.get_connection()
    try:
        added = ingest.sync_live_scrobbles(conn, lastfm)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to sync live scrobbles: {e}")
    finally:
        conn.close()
    return {"added": added}


@app.get("/api/scrobbles/timeline")
def get_scrobbles_timeline(days: int = 30):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT date(played_at) as day, COUNT(*) as plays FROM scrobbles "
            "WHERE played_at >= datetime('now', ?) GROUP BY day ORDER BY day",
            (f"-{days} days",),
        ).fetchall()
        return [{"day": r["day"], "plays": r["plays"]} for r in rows]
    finally:
        conn.close()


class AskRequest(BaseModel):
    question: str


# Cheap keyword gate before the (more expensive, more error-prone) LLM idea-extraction call — avoids
# misrouting genuine history questions that happen to mention an artist (e.g. "when did I start
# listening to Wilco") into the playlist-request path.
PLAYLIST_INTENT_KEYWORDS = ("playlist", "make me", "build me", "create a")


@app.post("/api/ask")
def post_ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    if any(kw in req.question.lower() for kw in PLAYLIST_INTENT_KEYWORDS):
        llm = _get_llm_connector()
        try:
            idea = llm.extract_idea_from_text(req.question)
        except Exception:
            idea = None
        if idea and idea.get("title") and idea.get("description"):
            return {"type": "playlist_idea", "idea": idea}

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT artist, track, played_at FROM scrobbles ORDER BY played_at DESC LIMIT ?",
            (ASK_SCROBBLE_LIMIT,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"type": "answer", "answer": "No listening history has been ingested yet — run a refresh first."}

    scrobbles_text = "\n".join(f"{r['played_at']} | {r['artist']} | {r['track']}" for r in rows)
    llm = _get_llm_connector()
    try:
        answer = llm.ask_about_history(req.question, scrobbles_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get an answer: {e}")
    return {"type": "answer", "answer": answer}


@app.get("/api/scrobbles/recent")
def get_scrobbles_recent(period: str = "24h"):
    now = datetime.now().astimezone()
    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "24h":
        from_dt = now - timedelta(hours=24)
    elif period == "7d":
        from_dt = now - timedelta(days=7)
    elif period == "30d":
        from_dt = now - timedelta(days=30)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown period '{period}'")

    lastfm = _get_lastfm_connector()
    try:
        tracks = lastfm.get_recent_tracks(from_ts=int(from_dt.timestamp()))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch recent tracks: {e}")
    return {"tracks": tracks, "period": period}


@app.post("/api/refresh")
def post_refresh():
    return ingest.run_ingest()


@app.post("/api/refresh-all")
def post_refresh_all():
    result = {"ingest": ingest.run_ingest()}
    spotify = _get_spotify_connector()
    if not spotify.is_authorized():
        result["purchases"] = {"skipped": True, "reason": "Spotify not connected."}
    else:
        try:
            result["purchases"] = _refresh_purchases(spotify)
        except Exception as exc:
            result["purchases"] = {"skipped": True, "reason": str(exc)}
    return result


@app.get("/api/playlists")
def get_playlists():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT playlist_name, COUNT(*) as track_count FROM playlist_tracks "
            "GROUP BY playlist_name ORDER BY playlist_name"
        ).fetchall()
        return [{"playlist_name": r["playlist_name"], "track_count": r["track_count"]} for r in rows]
    finally:
        conn.close()


@app.get("/api/playlists/{playlist_name}")
def get_playlist_tracks(playlist_name: str):
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT track_name, artist_names, album_name, added_at, popularity FROM playlist_tracks "
            "WHERE playlist_name = ? ORDER BY added_at DESC",
            (playlist_name,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"No playlist named '{playlist_name}'.")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/purchases")
def get_purchases():
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM purchase_items ORDER BY artist, album").fetchall()
        owned = {
            (r["artist_norm"], r["title_norm"])
            for r in conn.execute("SELECT artist_norm, title_norm FROM discogs_collection_cache").fetchall()
        }
        return [
            dict(r) for r in rows
            if (_normalize_match_key(r["artist"]), _normalize_match_key(r["album"])) not in owned
        ]
    finally:
        conn.close()


def _dominant_color_hex(image_bytes: bytes) -> str:
    """Simple average color, not true clustering — a fun visual artifact, not a rigorous analysis."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((50, 50))
    pixels = list(img.getdata())
    r, g, b = (sum(c[i] for c in pixels) // len(pixels) for i in range(3))
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


@app.get("/api/moodboard")
def get_moodboard():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT artist, album, spotify_album_id, image_url FROM purchase_items "
            "WHERE spotify_album_id IS NOT NULL AND image_url IS NOT NULL"
        ).fetchall()
        cached = {
            r["spotify_album_id"]: r["dominant_color_hex"]
            for r in conn.execute("SELECT spotify_album_id, dominant_color_hex FROM album_palette").fetchall()
        }
        now = datetime.now(timezone.utc).isoformat()
        results = []
        for row in rows:
            album_id = row["spotify_album_id"]
            color = cached.get(album_id)
            if not color:
                try:
                    resp = requests.get(row["image_url"], timeout=10)
                    resp.raise_for_status()
                    color = _dominant_color_hex(resp.content)
                except Exception:
                    continue
                conn.execute(
                    """
                    INSERT INTO album_palette (spotify_album_id, dominant_color_hex, fetched_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(spotify_album_id) DO UPDATE SET
                        dominant_color_hex=excluded.dominant_color_hex, fetched_at=excluded.fetched_at
                    """,
                    (album_id, color, now),
                )
                cached[album_id] = color
            r_, g_, b_ = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
            hue = colorsys.rgb_to_hsv(r_ / 255, g_ / 255, b_ / 255)[0]
            results.append({
                "artist": row["artist"], "album": row["album"],
                "image_url": row["image_url"], "dominant_color_hex": color, "hue": hue,
            })
        conn.commit()
        results.sort(key=lambda r: r["hue"])
        return results
    finally:
        conn.close()


@app.get("/api/purchases/recommendations")
def get_purchase_recommendations():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM purchase_recommendations ORDER BY score DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/purchases/refresh")
def post_purchases_refresh():
    spotify = _get_spotify_connector()
    if not spotify.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": spotify.get_auth_url()},
        )
    return _refresh_purchases(spotify)


def _normalize_match_key(s: str) -> str:
    """Best-effort match key for comparing a Spotify album to a Discogs collection release.

    Stripping parenthetical/bracketed content handles two unrelated quirks in one pass: Discogs's
    artist disambiguation suffix (e.g. "Real Estate (2)") and album edition suffixes (e.g. "Sunbather
    (10 Year Anniversary)"). A title that differs *outside* parens won't match — that's the safe
    failure direction (still shown as a purchase candidate, never wrongly hidden).
    """
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


def _refresh_purchases(spotify: SpotifyConnector) -> dict:
    discogs = _get_discogs_connector()
    bandcamp = BandcampConnector()
    albums = spotify.get_playlist_albums(FINDS_PLAYLIST_ID)

    now = datetime.now(timezone.utc).isoformat()
    conn = db.get_connection()
    try:
        for album in albums:
            try:
                match = discogs.search_release(album["artist"], album["album"])
            except Exception:
                match = None
            try:
                bc_match = bandcamp.search_album(album["artist"], album["album"])
            except Exception:
                bc_match = None
            conn.execute(
                """
                INSERT INTO purchase_items (
                    artist, album, spotify_album_id, image_url,
                    discogs_release_id, discogs_thumb_url, example_track, last_checked,
                    bandcamp_url, bandcamp_item_id, bandcamp_item_type, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist, album) DO UPDATE SET
                    spotify_album_id=excluded.spotify_album_id, image_url=excluded.image_url,
                    discogs_release_id=excluded.discogs_release_id, discogs_thumb_url=excluded.discogs_thumb_url,
                    example_track=excluded.example_track, last_checked=excluded.last_checked,
                    bandcamp_url=excluded.bandcamp_url, bandcamp_item_id=excluded.bandcamp_item_id,
                    bandcamp_item_type=excluded.bandcamp_item_type, added_at=excluded.added_at
                """,
                (
                    album["artist"], album["album"], album.get("spotify_album_id"), album.get("image_url"),
                    (match or {}).get("release_id"), (match or {}).get("thumb_url"),
                    album.get("example_track"), now,
                    (bc_match or {}).get("url"), (bc_match or {}).get("item_id"), (bc_match or {}).get("item_type"),
                    album.get("added_at"),
                ),
            )
        conn.commit()

        # Cache the user's real Discogs collection (normalized artist/title pairs only) so
        # "already owned" can be checked on every /api/purchases read without hitting Discogs live.
        collection_pairs = set()
        page = 1
        while True:
            try:
                page_data = discogs.get_collection_items(folder_id=0, page=page, per_page=100)
            except Exception:
                break
            for item in page_data.get("items", []):
                info = item.get("basic_information", {})
                artists = info.get("artists") or []
                if not artists or not info.get("title"):
                    continue
                collection_pairs.add((
                    _normalize_match_key(artists[0]["name"]),
                    _normalize_match_key(info["title"]),
                ))
            pagination = page_data.get("pagination", {})
            if page >= pagination.get("pages", 0):
                break
            page += 1

        conn.execute("DELETE FROM discogs_collection_cache")
        conn.executemany(
            "INSERT INTO discogs_collection_cache (artist_norm, title_norm, fetched_at) VALUES (?, ?, ?)",
            [(artist_norm, title_norm, now) for artist_norm, title_norm in collection_pairs],
        )
        conn.commit()

        lastfm = _get_lastfm_connector()
        weekly_plays = lastfm.get_weekly_top_artists()
        all_artists = {a["artist"]: a.get("example_track") for a in albums}
        seed_names_lower = {name.lower() for name in all_artists}
        seed_artists = {
            name: track
            for name, track in all_artists.items()
            if weekly_plays.get(name.lower(), 0) > 0
        }

        candidates = {}  # name -> {score, seed_artist}
        for seed in seed_artists:
            try:
                similar = lastfm.get_similar_artists(seed, limit=10)
            except Exception:
                continue
            for cand in similar:
                if cand["name"].lower() in seed_names_lower:
                    continue
                existing = candidates.get(cand["name"])
                if not existing or cand["score"] > existing["score"]:
                    candidates[cand["name"]] = {"score": cand["score"], "seed_artist": seed}

        ranked = sorted(candidates.items(), key=lambda kv: kv[1]["score"], reverse=True)
        ranked = [(n, c) for n, c in ranked if c["score"] >= RECOMMENDATION_SCORE_THRESHOLD][:MAX_RECOMMENDATIONS]

        conn.execute("DELETE FROM purchase_recommendations")
        for name, info in ranked:
            seed = info["seed_artist"]
            weekly_count = weekly_plays.get(seed.lower(), 0)
            if weekly_count > 0:
                reason = f"Because you've listened to {seed} {weekly_count} time{'s' if weekly_count != 1 else ''} this week"
            else:
                track = seed_artists.get(seed)
                reason = f'Because you liked "{track}" by {seed}' if track else f"Because you liked {seed}"
            conn.execute(
                """
                INSERT INTO purchase_recommendations (artist, reason, seed_artist, score, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artist) DO UPDATE SET
                    reason=excluded.reason, seed_artist=excluded.seed_artist,
                    score=excluded.score, fetched_at=excluded.fetched_at
                """,
                (name, reason, seed, info["score"], now),
            )
        conn.commit()
    finally:
        conn.close()

    return {"albums_synced": len(albums), "recommendations": len(ranked)}


@app.get("/api/shows")
def get_shows():
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM tour_events ORDER BY event_date").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class ShowStatusRequest(BaseModel):
    status: str


@app.post("/api/shows/{event_id}/status")
def post_show_status(event_id: int, req: ShowStatusRequest):
    if req.status not in {"new", "interested", "going", "passed"}:
        raise HTTPException(status_code=422, detail="Status must be one of: new, interested, going, passed.")
    conn = db.get_connection()
    try:
        conn.execute("UPDATE tour_events SET status = ? WHERE id = ?", (req.status, event_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/shows/{event_id}")
def delete_show(event_id: int):
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM tour_events WHERE id = ?", (event_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/shows/refresh")
def post_shows_refresh():
    spotify = _get_spotify_connector()
    if not spotify.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": spotify.get_auth_url()},
        )
    ticketmaster = _get_ticketmaster_connector()

    conn = db.get_connection()
    try:
        profile_row = conn.execute(
            "SELECT defining_artists_json FROM taste_profile_snapshots WHERE snapshot_date = 'CURRENT'"
        ).fetchone()
        durable_artists = set(json.loads(profile_row["defining_artists_json"] or "[]")) if profile_row else set()

        albums = spotify.get_playlist_albums(FINDS_PLAYLIST_ID)
        recent_artists = {a["artist"] for a in albums}

        artists_to_check = {(name, "durable_artist") for name in durable_artists}
        artists_to_check |= {(name, "recent_find") for name in recent_artists if name not in durable_artists}

        now = datetime.now(timezone.utc).isoformat()
        events_found = 0
        for artist, confidence in artists_to_check:
            try:
                events = ticketmaster.search_events_for_artist(artist)
            except Exception:
                continue
            for e in events:
                if not e.get("ticketmaster_id"):
                    continue
                conn.execute(
                    """
                    INSERT INTO tour_events (
                        artist, event_name, event_date, venue, city,
                        ticketmaster_url, ticketmaster_id, confidence, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticketmaster_id) DO UPDATE SET
                        event_name=excluded.event_name, event_date=excluded.event_date,
                        venue=excluded.venue, city=excluded.city, ticketmaster_url=excluded.ticketmaster_url,
                        confidence=excluded.confidence, fetched_at=excluded.fetched_at
                    """,
                    (
                        artist, e["event_name"], e["event_date"], e["venue"], e["city"],
                        e["url"], e["ticketmaster_id"], confidence, now,
                    ),
                )
                events_found += 1

        # Songkick-tracked shows are a separate, independent source from the artist-driven Ticketmaster
        # search above — they can be for any show the user tracked, including artists MusicDash has no
        # taste signal for at all. No DB-level UNIQUE constraint on songkick_uid (SQLite can't add one
        # to an existing column without a table rebuild), so dedup is a check-then-insert/update here.
        songkick_found = 0
        try:
            songkick = _get_songkick_connector()
            tracked = songkick.get_tracked_events()
        except Exception:
            tracked = []
        for e in tracked:
            if not e.get("songkick_uid"):
                continue
            existing = conn.execute(
                "SELECT id FROM tour_events WHERE songkick_uid = ?", (e["songkick_uid"],)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE tour_events SET
                        artist=?, event_name=?, event_date=?, venue=?, city=?,
                        ticketmaster_url=?, fetched_at=?
                    WHERE id = ?
                    """,
                    (e["artist"], e["event_name"], e["event_date"], e["venue"], e["city"],
                     e["url"], now, existing["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO tour_events (
                        artist, event_name, event_date, venue, city, ticketmaster_url,
                        confidence, source, songkick_uid, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'self_tracked', 'songkick', ?, ?)
                    """,
                    (e["artist"], e["event_name"], e["event_date"], e["venue"], e["city"],
                     e["url"], e["songkick_uid"], now),
                )
            songkick_found += 1
        conn.commit()
    finally:
        conn.close()
    return {
        "artists_checked": len(artists_to_check),
        "ticketmaster_events_found": events_found,
        "songkick_events_found": songkick_found,
    }


class ManualShowRequest(BaseModel):
    artist: str
    event_name: str
    event_date: str
    venue: str = ""
    city: str = ""
    url: str = ""


@app.post("/api/shows/manual")
def post_manual_show(req: ManualShowRequest):
    conn = db.get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO tour_events (
                artist, event_name, event_date, venue, city, ticketmaster_url,
                confidence, source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'self_tracked', 'manual', ?)
            """,
            (req.artist, req.event_name, req.event_date, req.venue, req.city, req.url, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/shows/{event_id}/preshow-playlist")
def post_preshow_playlist(event_id: int):
    spotify = _get_spotify_connector()
    if not spotify.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": spotify.get_auth_url()},
        )
    setlistfm = _get_setlistfm_connector()

    conn = db.get_connection()
    try:
        event = conn.execute("SELECT * FROM tour_events WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    if not event:
        raise HTTPException(status_code=404, detail="Show not found.")

    try:
        setlists = setlistfm.get_recent_setlists(event["artist"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch setlists: {e}")
    if not setlists:
        raise HTTPException(status_code=404, detail="No recent setlists found for this artist on setlist.fm.")

    # Rank by frequency across the artist's last few real shows — songs they're playing every night
    # come first, not just whatever happened to be in the most recent single setlist.
    song_counts = {}
    for setlist in setlists:
        for song in setlist["songs"]:
            song_counts[song] = song_counts.get(song, 0) + 1
    ranked_songs = sorted(song_counts.items(), key=lambda kv: kv[1], reverse=True)

    seen_uris = set()
    track_uris = []
    for song_name, _ in ranked_songs:
        try:
            match = spotify.search_track_by_title_and_artist(song_name, event["artist"])
        except Exception:
            continue
        if match and match["uri"] not in seen_uris:
            seen_uris.add(match["uri"])
            track_uris.append(match["uri"])

    if not track_uris:
        raise HTTPException(status_code=404, detail="Couldn't match any setlist songs to Spotify tracks.")

    conn = db.get_connection()
    try:
        # Reuse the existing pre-show playlist for this event if "Build" is clicked again, rather than
        # creating a fresh duplicate each time — same reuse pattern as the playlist-idea flow.
        if event["preshow_playlist_id"]:
            playlist_id, playlist_url = event["preshow_playlist_id"], event["preshow_playlist_url"]
            try:
                existing_uris = spotify.get_playlist_track_uris(playlist_id)
                spotify.add_tracks_to_playlist(playlist_id, [u for u in track_uris if u not in existing_uris])
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to update playlist: {e}")
        else:
            title = f"{event['artist']} — Pre-Show ({event['event_date']})"
            try:
                playlist = spotify.create_playlist(title, f"Songs from {event['artist']}'s recent live setlists.")
                spotify.add_tracks_to_playlist(playlist["id"], track_uris)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to create playlist: {e}")
            playlist_id, playlist_url = playlist["id"], playlist["external_urls"]["spotify"]
            conn.execute(
                "UPDATE tour_events SET preshow_playlist_id = ?, preshow_playlist_url = ? WHERE id = ?",
                (playlist_id, playlist_url, event_id),
            )
            conn.commit()
    finally:
        conn.close()

    return {"playlist_url": playlist_url, "track_count": len(track_uris)}


class RateItemRequest(BaseModel):
    folder_id: int
    instance_id: int
    rating: int


class MoveItemRequest(BaseModel):
    folder_id: int
    instance_id: int
    destination_folder_id: int


class AddItemRequest(BaseModel):
    release_id: int
    folder_id: int = 1


@app.get("/api/collection/folders")
def get_collection_folders():
    discogs = _get_discogs_connector()
    try:
        return discogs.get_collection_folders()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Discogs folders: {e}")


@app.get("/api/collection/items")
def get_collection_items(
    folder_id: int = 0, page: int = 1, per_page: int = 50,
    sort: str = "artist", sort_order: str = "asc",
):
    discogs = _get_discogs_connector()
    try:
        return discogs.get_collection_items(folder_id, page, per_page, sort, sort_order)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch collection items: {e}")


@app.post("/api/collection/items/{release_id}/rate")
def post_collection_item_rate(release_id: int, req: RateItemRequest):
    discogs = _get_discogs_connector()
    try:
        discogs.rate_item(req.folder_id, release_id, req.instance_id, req.rating)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to rate item: {e}")
    return {"ok": True}


@app.post("/api/collection/items/{release_id}/move")
def post_collection_item_move(release_id: int, req: MoveItemRequest):
    discogs = _get_discogs_connector()
    try:
        discogs.move_item(req.folder_id, release_id, req.instance_id, req.destination_folder_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to move item: {e}")
    return {"ok": True}


@app.delete("/api/collection/items/{release_id}")
def delete_collection_item(release_id: int, folder_id: int, instance_id: int):
    discogs = _get_discogs_connector()
    try:
        discogs.remove_item(folder_id, release_id, instance_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to remove item: {e}")
    return {"ok": True}


@app.get("/api/collection/search")
def get_collection_search(q: str):
    discogs = _get_discogs_connector()
    try:
        return discogs.search_releases(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Discogs search failed: {e}")


@app.post("/api/collection/items")
def post_collection_item_add(req: AddItemRequest):
    discogs = _get_discogs_connector()
    try:
        return discogs.add_item(req.folder_id, req.release_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to add item: {e}")


class PlaylistIdeaRequest(BaseModel):
    title: str
    description: str = ""


def _search_and_add_tracks(
    conn, connector: SpotifyConnector, lastfm: LastfmConnector,
    title: str, description: str, playlist_id: str, current_uris: set, rejected_artists: set,
) -> dict:
    """Searches for new candidate tracks for an idea and adds them to the live playlist + DB log.
    Shared by the initial "Create on Spotify" flow and "Regenerate" — the only difference between
    them is what `current_uris`/DB rows look like going in (regenerate clears both first)."""
    try:
        disabled_categories = _disabled_categories(_category_rejection_rates(conn))
        candidates = _gather_playlist_candidates(
            connector, lastfm, title, description, max_tracks=20,
            disabled_categories=disabled_categories,
        )
        known_uris = _known_discovery_track_uris(connector, exclude_playlist_id=playlist_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to search for tracks: {e}")

    new_tracks = [
        c for c in candidates
        if c["uri"] not in current_uris
        and c["uri"] not in known_uris
        and c["artist"].lower() not in rejected_artists
    ]

    try:
        connector.add_tracks_to_playlist(playlist_id, [t["uri"] for t in new_tracks])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to add tracks: {e}")

    now = datetime.now(timezone.utc).isoformat()
    for t in new_tracks:
        conn.execute(
            """
            INSERT INTO playlist_idea_tracks
                (idea_title, track_uri, track_name, artist_name, reason, category, confidence, status, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'added', ?)
            ON CONFLICT(idea_title, track_uri) DO UPDATE SET
                status='added', reason=excluded.reason, category=excluded.category,
                confidence=excluded.confidence, added_at=excluded.added_at
            """,
            (title, t["uri"], t["name"], t["artist"], t["reason"], t["category"], t["confidence"], now),
        )
    conn.commit()
    return {"tracks": new_tracks, "disabled_categories": sorted(disabled_categories)}


def _classify_idea_terms(description: str) -> tuple:
    """Splits a playlist idea's description into artist names vs. genre/trait words.

    Every idea in CURRENT.md writes artist names capitalized ("Delta Sleep", "Hotline TNT") and
    genre/trait words lowercase ("post-rock", "angular, technical guitar interplay") — this holds
    across every idea checked. Capitalization of the first character is a simpler, more reliable
    signal than querying Spotify to guess (genre words can collide with real obscure artist names,
    e.g. "chillwave" is also a real artist on Spotify — confirmed live).
    """
    raw_terms = re.split(r"[,:]", description)
    terms = [t.strip().rstrip(".") for t in raw_terms if t.strip()]
    artist_terms = [t for t in terms if t[0].isupper()]
    genre_terms = [t for t in terms if not t[0].isupper()]
    return artist_terms, genre_terms


def _normalize_genre_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _artist_passes_tag_check(lastfm: LastfmConnector, artist_name: str, genre_words: list) -> bool:
    """Sanity filter for genre/freetext-sourced candidates: does this artist's Last.fm tags actually
    overlap with the idea's genre words? Skipped for direct artist-seed matches, which are already
    precise. Fails open (allows the track through) on Last.fm errors or an idea with no genre words at
    all, since this is a secondary safety net, not the primary relevance signal.

    Requires exact match after stripping spaces/hyphens (so "post-rock" == "post rock"), not substring
    containment — a naive substring check let Foo Fighters ("Everlong") through under "post-rock"
    because their real tag is plain "rock", which is textually a substring of "post-rock"/"krautrock".
    Confirmed live: Foo Fighters' top Last.fm tags are rock/alternative rock/grunge, no post-rock."""
    if not genre_words:
        return True
    try:
        tags = [_normalize_genre_word(t) for t in lastfm.get_artist_top_tags(artist_name, limit=10)]
    except Exception:
        return True
    if not tags:
        return True
    normalized_words = [_normalize_genre_word(gw) for gw in genre_words]
    return any(gw == tag for gw in normalized_words for tag in tags)


# Confidence is a deterministic score from *how* a candidate was found, not a learned value —
# seed-artist matches are exact-name-verified (highest certainty); similar-artist expansions scale
# with Last.fm's own 0-1 match score; genre-tag and free-text matches only ever get this far after
# passing the tag cross-check, but that's an indirect signal, hence the lower flat scores.
CATEGORY_BASE_CONFIDENCE = {
    "seed_artist": 1.0,
    "similar_artist": None,  # scaled by Last.fm match score instead of a flat value
    "genre_tag": 0.6,
    "text_match": 0.4,
}


def _gather_playlist_candidates(
    spotify: SpotifyConnector, lastfm: LastfmConnector, title: str, description: str, max_tracks: int,
    disabled_categories: set = frozenset(),
) -> list:
    """Builds a list of {uri, name, artist, reason, category, confidence} candidates for a playlist idea.

    Priority order: direct artist-seed matches, then Last.fm-similar expansions of those seeds, then
    genre-tag matches, then free-text fallback — each genre/freetext candidate is checked against the
    artist's real Last.fm tags before being kept. `disabled_categories` lets the rejection-rate-based
    strategy weighting (see `_category_rejection_rates`) skip a category entirely once it's proven to
    be mostly wrong historically, rather than searching it every time just to filter it out later.

    At most one track per artist survives, full stop — per-category limits (e.g.
    `SIMILAR_ARTIST_TRACK_LIMIT`) allow more than one track per artist *within* a single category, and
    nothing stopped the same artist turning up again across categories (seed, then also similar-artist
    of a different seed, then also a genre/text match). `seen_artists` is the cross-category guard;
    since categories run in priority order, the first (highest-priority) track for an artist wins and
    later ones from any category are dropped.
    """
    artist_terms, genre_terms = _classify_idea_terms(description)
    if not artist_terms and not genre_terms:
        genre_terms = [title]

    seen_uris = set()
    seen_artists = set()
    candidates = []

    def add(items: list, category: str, confidence: float, reason_fn) -> None:
        if category in disabled_categories:
            return
        for t in items:
            if t["uri"] in seen_uris:
                continue
            artist_key = t["artists"][0]["name"].lower()
            if artist_key in seen_artists:
                continue
            seen_uris.add(t["uri"])
            seen_artists.add(artist_key)
            candidates.append({
                "uri": t["uri"], "name": t["name"], "artist": t["artists"][0]["name"],
                "reason": reason_fn(t), "category": category, "confidence": confidence,
            })

    confirmed_artists = []
    for term in artist_terms:
        items = spotify.search_tracks_by_artist(term, ARTIST_SEED_TRACK_LIMIT)
        if items:
            confirmed_artists.append(term)
            add(items, "seed_artist", CATEGORY_BASE_CONFIDENCE["seed_artist"],
                lambda t, term=term: f'seed artist "{term}"')
        else:
            genre_terms.append(term)  # not a confirmed artist after all — fall back to text search

    for seed in confirmed_artists:
        try:
            similar = lastfm.get_similar_artists(seed, limit=10)
        except Exception:
            continue
        expanded = 0
        for cand in similar:
            if expanded >= SIMILAR_ARTIST_EXPANSION:
                break
            name = cand["name"]
            if name.lower() in {a.lower() for a in artist_terms}:
                continue
            items = spotify.search_tracks_by_artist(name, SIMILAR_ARTIST_TRACK_LIMIT)
            if items:
                expanded += 1
                add(items, "similar_artist", cand["score"],
                    lambda t, seed=seed, name=name, score=cand["score"]:
                        f'similar artist "{name}" of seed "{seed}" (Last.fm match {score:.2f})')

    genre_words = [g.lower() for g in genre_terms]
    for term in genre_terms:
        genre_items = [
            t for t in spotify.search_tracks_by_genre(term, GENRE_TERM_TRACK_LIMIT)
            if _artist_passes_tag_check(lastfm, t["artists"][0]["name"], genre_words)
        ]
        add(genre_items, "genre_tag", CATEGORY_BASE_CONFIDENCE["genre_tag"],
            lambda t, term=term: f'genre tag "{term}"')

        text_items = [
            t for t in spotify.search_tracks_freetext(term, GENRE_TERM_TRACK_LIMIT)
            if _artist_passes_tag_check(lastfm, t["artists"][0]["name"], genre_words)
        ]
        add(text_items, "text_match", CATEGORY_BASE_CONFIDENCE["text_match"],
            lambda t, term=term: f'text match "{term}"')

    return candidates[:max_tracks]


def _category_rejection_rates(conn) -> dict:
    """Historical added-vs-rejected outcomes per search category, across every idea ever created.
    This is the closest honest thing to "the system learns" here: a simple frequency count of which
    search strategies actually hold up over time, not a trained model."""
    rows = conn.execute(
        "SELECT category, status, COUNT(*) as n FROM playlist_idea_tracks GROUP BY category, status"
    ).fetchall()
    counts = {}
    for r in rows:
        counts.setdefault(r["category"], {"added": 0, "rejected": 0})[r["status"]] = r["n"]
    rates = {}
    for category, c in counts.items():
        total = c["added"] + c["rejected"]
        rates[category] = {
            "total": total, "rejected": c["rejected"],
            "rejection_rate": c["rejected"] / total if total else 0.0,
        }
    return rates


def _disabled_categories(rates: dict) -> set:
    return {
        category for category, r in rates.items()
        if category not in STRATEGY_EXEMPT_CATEGORIES
        and r["total"] >= STRATEGY_MIN_SAMPLE_SIZE
        and r["rejection_rate"] >= STRATEGY_REJECTION_THRESHOLD
    }


@app.get("/api/spotify/playlists/strategy-stats")
def get_spotify_playlists_strategy_stats():
    conn = db.get_connection()
    try:
        rates = _category_rejection_rates(conn)
    finally:
        conn.close()
    disabled = _disabled_categories(rates)
    return {
        "categories": {
            category: {**r, "disabled": category in disabled}
            for category, r in rates.items()
        },
    }


def _known_discovery_track_uris(spotify: SpotifyConnector, exclude_playlist_id: str = None) -> set:
    """Track URIs already sitting in the established discovery playlists, for dedup."""
    uris = set()
    for p in spotify.get_user_playlists():
        if p["name"] in DISCOVERY_PLAYLIST_NAMES and p["id"] != exclude_playlist_id:
            try:
                uris |= spotify.get_playlist_track_uris(p["id"])
            except Exception:
                continue
    return uris


def _idea_descriptions(conn) -> dict:
    row = conn.execute(
        "SELECT playlist_ideas_json FROM taste_profile_snapshots WHERE snapshot_date = 'CURRENT'"
    ).fetchone()
    ideas = json.loads(row["playlist_ideas_json"] or "[]") if row else []
    return {i["title"]: i.get("description", "") for i in ideas}


SEED_ARTIST_RE = re.compile(r'seed artist "([^"]+)"')
SIMILAR_ARTIST_OF_RE = re.compile(r'similar artist "([^"]+)" of seed "([^"]+)"')
SIMILAR_SEED_ONLY_RE = re.compile(r'similar to seed artist "([^"]+)"')  # pre-fix reason format


def _audit_track(lastfm: LastfmConnector, row, genre_words: list) -> str:
    """Re-validates one logged track against current rules. Returns a suspect reason, or '' if clean."""
    category, artist_name, reason = row["category"], row["artist_name"], row["reason"]

    if category == "seed_artist":
        m = SEED_ARTIST_RE.search(reason)
        if m and m.group(1).lower() != artist_name.lower():
            return f'logged as seed artist "{m.group(1)}" but track is actually by "{artist_name}"'
        return ""

    if category == "similar_artist":
        m = SIMILAR_ARTIST_OF_RE.search(reason)
        if m:
            claimed_name = m.group(1)
            if claimed_name.lower() != artist_name.lower():
                return f'logged as similar artist "{claimed_name}" but track is actually by "{artist_name}"'
            return ""
        # Pre-fix rows only recorded the seed, not which similar artist was actually searched —
        # re-derive by checking whether artist_name is still a real Last.fm similar match of that seed.
        m = SIMILAR_SEED_ONLY_RE.search(reason)
        if not m:
            return ""
        seed = m.group(1)
        if artist_name.lower() == seed.lower():
            return ""
        try:
            similar_names = {a["name"].lower() for a in lastfm.get_similar_artists(seed, limit=15)}
        except Exception:
            return ""
        if artist_name.lower() not in similar_names:
            return f'logged as similar to seed "{seed}" but "{artist_name}" isn\'t in its current Last.fm similar-artist list'
        return ""

    # genre_tag / text_match
    if not _artist_passes_tag_check(lastfm, artist_name, genre_words):
        return f'no longer passes the genre tag check for {genre_words} under current rules'
    return ""


def _run_playlist_audit(spotify: SpotifyConnector, lastfm: LastfmConnector) -> list:
    """Re-validates every logged 'added' track across every idea-playlist against current rules.
    Read-only — flags suspects for review, never removes anything itself."""
    conn = db.get_connection()
    try:
        descriptions = _idea_descriptions(conn)
        rows = conn.execute(
            "SELECT * FROM playlist_idea_tracks WHERE status = 'added' ORDER BY idea_title"
        ).fetchall()
    finally:
        conn.close()

    flagged = []
    genre_words_cache = {}
    for row in rows:
        idea_title = row["idea_title"]
        if idea_title not in genre_words_cache:
            _, genre_terms = _classify_idea_terms(descriptions.get(idea_title, ""))
            genre_words_cache[idea_title] = [g.lower() for g in genre_terms]

        suspect_reason = _audit_track(lastfm, row, genre_words_cache[idea_title])
        if suspect_reason:
            flagged.append({
                "idea_title": idea_title, "track_uri": row["track_uri"], "track_name": row["track_name"],
                "artist_name": row["artist_name"], "reason": row["reason"], "category": row["category"],
                "suspect_reason": suspect_reason,
            })
    return flagged


@app.get("/api/spotify/playlists/audit")
def get_spotify_playlists_audit():
    connector = _get_spotify_connector()
    if not connector.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": connector.get_auth_url()},
        )
    lastfm = _get_lastfm_connector()
    try:
        flagged = _run_playlist_audit(connector, lastfm)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Audit failed: {e}")
    return {"flagged": flagged, "count": len(flagged)}


class AuditRemoveRequest(BaseModel):
    idea_title: str
    track_uri: str


@app.post("/api/spotify/playlists/audit/remove")
def post_spotify_playlists_audit_remove(req: AuditRemoveRequest):
    connector = _get_spotify_connector()
    if not connector.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": connector.get_auth_url()},
        )

    conn = db.get_connection()
    try:
        playlist_row = conn.execute(
            "SELECT playlist_id FROM playlist_idea_playlists WHERE idea_title = ?", (req.idea_title,)
        ).fetchone()
        if not playlist_row:
            raise HTTPException(status_code=404, detail=f'No playlist found for idea "{req.idea_title}".')

        try:
            connector.remove_tracks_from_playlist(playlist_row["playlist_id"], [req.track_uri])
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to remove track: {e}")

        conn.execute(
            "UPDATE playlist_idea_tracks SET status = 'rejected' WHERE idea_title = ? AND track_uri = ?",
            (req.idea_title, req.track_uri),
        )
        conn.commit()
    finally:
        conn.close()
    return {"removed": True}


@app.get("/api/spotify/status")
def get_spotify_status():
    configured = bool(
        os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET") and os.getenv("SPOTIFY_REDIRECT_URI")
    )
    if not configured:
        return {"configured": False, "authorized": False}
    connector = _get_spotify_connector()
    return {"configured": True, "authorized": connector.is_authorized()}


@app.get("/api/spotify/auth-url")
def get_spotify_auth_url():
    connector = _get_spotify_connector()
    return {"url": connector.get_auth_url()}


@app.get("/callback")
def spotify_callback(code: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify authorization failed: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    connector = _get_spotify_connector()
    connector.handle_callback(code)
    return RedirectResponse(url="/")


@app.get("/api/spotify/artist-images")
def get_spotify_artist_images(names: str):
    artist_names = [n.strip() for n in names.split(",") if n.strip()]
    if not artist_names:
        return {}

    conn = db.get_connection()
    try:
        result = {}
        to_fetch = []
        for name in artist_names:
            row = conn.execute(
                "SELECT * FROM artist_images WHERE artist_name = ?", (name,)
            ).fetchone()
            if row and (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).days < ARTIST_IMAGE_TTL_DAYS:
                result[name] = {"image_url": row["image_url"], "spotify_id": row["spotify_id"]}
            else:
                to_fetch.append(name)

        if to_fetch and os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"):
            connector = _get_spotify_connector()
            now = datetime.now(timezone.utc).isoformat()
            for name in to_fetch:
                info = connector.get_artist_image(name) or {"spotify_id": None, "image_url": None}
                conn.execute(
                    """
                    INSERT INTO artist_images (artist_name, spotify_id, image_url, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(artist_name) DO UPDATE SET
                        spotify_id=excluded.spotify_id, image_url=excluded.image_url, fetched_at=excluded.fetched_at
                    """,
                    (name, info.get("spotify_id"), info.get("image_url"), now),
                )
                result[name] = {"image_url": info.get("image_url"), "spotify_id": info.get("spotify_id")}
            conn.commit()
        return result
    finally:
        conn.close()


@app.post("/api/spotify/playlists/from-idea")
def post_spotify_playlist_from_idea(req: PlaylistIdeaRequest):
    connector = _get_spotify_connector()
    if not connector.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": connector.get_auth_url()},
        )
    lastfm = _get_lastfm_connector()

    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM playlist_idea_playlists WHERE idea_title = ?", (req.title,)
        ).fetchone()

        reused = False
        current_uris = set()
        if existing:
            try:
                current_uris = connector.get_playlist_track_uris(existing["playlist_id"])
                playlist_id, playlist_url = existing["playlist_id"], existing["playlist_url"]
                reused = True
            except Exception:
                existing = None  # playlist no longer exists on Spotify — recreate below

        if not existing:
            try:
                playlist = connector.create_playlist(req.title, req.description)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to create playlist: {e}")
            playlist_id = playlist["id"]
            playlist_url = playlist["external_urls"]["spotify"]
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO playlist_idea_playlists (idea_title, playlist_id, playlist_url, created_at) "
                "VALUES (?, ?, ?, ?)",
                (req.title, playlist_id, playlist_url, now),
            )
            conn.commit()

        # Self-correcting feedback loop: any track we previously added that the user has since removed
        # from the live playlist is a negative signal — exclude that artist from this idea going forward.
        logged_added = conn.execute(
            "SELECT track_uri, artist_name FROM playlist_idea_tracks WHERE idea_title = ? AND status = 'added'",
            (req.title,),
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in logged_added:
            if row["track_uri"] not in current_uris:
                conn.execute(
                    "UPDATE playlist_idea_tracks SET status = 'rejected' WHERE idea_title = ? AND track_uri = ?",
                    (req.title, row["track_uri"]),
                )
        conn.commit()

        rejected_artists = {
            row["artist_name"].lower()
            for row in conn.execute(
                "SELECT artist_name FROM playlist_idea_tracks WHERE idea_title = ? AND status = 'rejected'",
                (req.title,),
            ).fetchall()
        }

        result = _search_and_add_tracks(
            conn, connector, lastfm, req.title, req.description, playlist_id, current_uris, rejected_artists,
        )

        return {
            "playlist_url": playlist_url,
            "track_count": len(result["tracks"]),
            "tracks": result["tracks"],
            "reused_existing_playlist": reused,
            "excluded_rejected_artists": sorted(rejected_artists),
            "disabled_categories": result["disabled_categories"],
        }
    finally:
        conn.close()


@app.post("/api/playlist-ideas/liner-notes")
def post_playlist_idea_liner_notes(req: PlaylistIdeaRequest, regenerate: bool = False):
    """Idea titles can contain "/" (e.g. "Emo Revival / Shoegaze Bridge") so title+description travel in
    the request body rather than a URL path param, same shape as the existing PlaylistIdeaRequest."""
    conn = db.get_connection()
    try:
        if not regenerate:
            cached = conn.execute(
                "SELECT liner_notes FROM playlist_liner_notes WHERE idea_title = ?", (req.title,)
            ).fetchone()
            if cached:
                return {"liner_notes": cached["liner_notes"], "cached": True}

        track_rows = conn.execute(
            "SELECT track_name, artist_name FROM playlist_idea_tracks WHERE idea_title = ? AND status = 'added'",
            (req.title,),
        ).fetchall()
        if not track_rows:
            raise HTTPException(
                status_code=422,
                detail="No tracks found for this playlist yet — create it on Spotify first.",
            )
        tracks = [{"name": r["track_name"], "artist": r["artist_name"]} for r in track_rows]

        llm = _get_llm_connector()
        try:
            notes = llm.generate_liner_notes(req.title, req.description, tracks)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to generate liner notes: {e}")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO playlist_liner_notes (idea_title, liner_notes, generated_at) VALUES (?, ?, ?)
            ON CONFLICT(idea_title) DO UPDATE SET
                liner_notes=excluded.liner_notes, generated_at=excluded.generated_at
            """,
            (req.title, notes, now),
        )
        conn.commit()
        return {"liner_notes": notes, "cached": False}
    finally:
        conn.close()


class GenreNoteRequest(BaseModel):
    genre_note: str


def _run_second_opinion_review(idea_title: str, idea_description: str, result: dict) -> dict:
    """Second-opinion gate: an independent model (Llama via Groq) reviews the candidates Claude's
    pipeline just added. Flags rather than auto-removes — an earlier version let this auto-remove, but
    it was overly trigger-happy on legitimate, simply-unfamiliar artists, so it's surfaced for manual
    review (same pattern as the existing playlist audit) instead of trusted unsupervised."""
    if isinstance(result, JSONResponse) or not result.get("tracks"):
        return result
    try:
        groq = _get_groq_connector()
        flagged = groq.review_tracks(idea_title, idea_description, result["tracks"])
    except Exception as e:
        result["second_opinion_error"] = str(e)
        return result

    tracks_by_uri = {t["uri"]: t for t in result["tracks"]}
    result["second_opinion_flagged"] = [
        {**tracks_by_uri[f["uri"]], "second_opinion_reason": f["reason"], "idea_title": idea_title}
        for f in flagged if f.get("uri") in tracks_by_uri
    ]
    return result


def _get_or_extract_field_trip_idea(conn, genre_note: str) -> tuple:
    """Field-trip genre notes are often full prose, and the LLM extraction into a clean {title,
    description} isn't guaranteed deterministic across calls — so the note's exact text is cached to a
    stable idea title/description the first time, letting 'Try Again' reliably target the same
    playlist instead of risking a new title (and a new, separate playlist) each time."""
    cached = conn.execute(
        "SELECT idea_title, idea_description FROM field_trip_playlists WHERE genre_note = ?", (genre_note,)
    ).fetchone()
    if cached:
        return cached["idea_title"], cached["idea_description"], True

    llm = _get_llm_connector()
    try:
        idea = llm.extract_idea_from_text(genre_note)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to interpret genre note: {e}")
    if not idea.get("title") or not idea.get("description"):
        raise HTTPException(status_code=502, detail="Could not extract a clean idea from this genre note.")
    return idea["title"], idea["description"], False


def _cache_field_trip_playlist(conn, genre_note: str, title: str, description: str, result: dict) -> None:
    playlist_row = conn.execute(
        "SELECT playlist_id FROM playlist_idea_playlists WHERE idea_title = ?", (title,)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO field_trip_playlists
            (genre_note, idea_title, idea_description, playlist_id, playlist_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(genre_note) DO UPDATE SET
            idea_title=excluded.idea_title, idea_description=excluded.idea_description,
            playlist_id=excluded.playlist_id, playlist_url=excluded.playlist_url, created_at=excluded.created_at
        """,
        (genre_note, title, description, playlist_row["playlist_id"], result["playlist_url"],
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


@app.get("/api/spotify/playlists/field-trip-tracks")
def get_field_trip_tracks(genre_note: str):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT playlist_id FROM field_trip_playlists WHERE genre_note = ?", (genre_note,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"tracks": []}

    connector = _get_spotify_connector()
    if not connector.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": connector.get_auth_url()},
        )
    try:
        tracks = connector.get_playlist_tracks(row["playlist_id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch playlist tracks: {e}")
    return {"tracks": tracks}


@app.post("/api/spotify/playlists/from-genre-note")
def post_spotify_playlist_from_genre_note(req: GenreNoteRequest):
    """'Next Field Trip Genre' notes are often full prose (mixing genre names, real artists, and
    meta-commentary about the note's own confidence), not the clean artist/genre tag lists playlist
    ideas use — feeding that prose straight into _gather_playlist_candidates returns fuzzy-text-search
    noise. Distill it into the same {title, description} shape first, then reuse the normal flow."""
    conn = db.get_connection()
    try:
        title, description, was_cached = _get_or_extract_field_trip_idea(conn, req.genre_note)
    finally:
        conn.close()

    result = post_spotify_playlist_from_idea(PlaylistIdeaRequest(title=title, description=description))

    if not was_cached and not isinstance(result, JSONResponse) and result.get("playlist_url"):
        conn = db.get_connection()
        try:
            _cache_field_trip_playlist(conn, req.genre_note, title, description, result)
        finally:
            conn.close()

    return _run_second_opinion_review(title, description, result)


@app.post("/api/spotify/playlists/from-genre-note/regenerate")
def post_spotify_playlist_from_genre_note_regenerate(req: GenreNoteRequest):
    conn = db.get_connection()
    try:
        cached = conn.execute(
            "SELECT idea_title, idea_description FROM field_trip_playlists WHERE genre_note = ?",
            (req.genre_note,),
        ).fetchone()
    finally:
        conn.close()
    if not cached:
        raise HTTPException(
            status_code=404, detail="No playlist exists yet for this genre note — use Try This first."
        )

    result = post_spotify_playlist_from_idea_regenerate(
        PlaylistIdeaRequest(title=cached["idea_title"], description=cached["idea_description"])
    )
    return _run_second_opinion_review(cached["idea_title"], cached["idea_description"], result)


@app.post("/api/spotify/playlists/from-idea/regenerate")
def post_spotify_playlist_from_idea_regenerate(req: PlaylistIdeaRequest):
    """Clears every track currently in the idea's live playlist and searches fresh, instead of the
    incremental top-up that 'Create on Spotify' does. Keeps prior explicit rejections (from the audit's
    'Remove from playlist') so artists already weeded out don't just come back."""
    connector = _get_spotify_connector()
    if not connector.is_authorized():
        return JSONResponse(
            status_code=401,
            content={"detail": "Spotify not connected.", "auth_url": connector.get_auth_url()},
        )
    lastfm = _get_lastfm_connector()

    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM playlist_idea_playlists WHERE idea_title = ?", (req.title,)
        ).fetchone()
        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f'No playlist exists yet for "{req.title}" — use Create on Spotify first.',
            )
        playlist_id, playlist_url = existing["playlist_id"], existing["playlist_url"]

        try:
            live_uris = connector.get_playlist_track_uris(playlist_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to read current playlist tracks: {e}")

        if live_uris:
            try:
                connector.remove_tracks_from_playlist(playlist_id, list(live_uris))
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Failed to clear current tracks: {e}")

        conn.execute(
            "DELETE FROM playlist_idea_tracks WHERE idea_title = ? AND status = 'added'", (req.title,)
        )
        conn.commit()

        rejected_artists = {
            row["artist_name"].lower()
            for row in conn.execute(
                "SELECT artist_name FROM playlist_idea_tracks WHERE idea_title = ? AND status = 'rejected'",
                (req.title,),
            ).fetchall()
        }

        result = _search_and_add_tracks(
            conn, connector, lastfm, req.title, req.description, playlist_id, set(), rejected_artists,
        )

        return {
            "playlist_url": playlist_url,
            "track_count": len(result["tracks"]),
            "tracks": result["tracks"],
            "excluded_rejected_artists": sorted(rejected_artists),
            "disabled_categories": result["disabled_categories"],
        }
    finally:
        conn.close()


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def _serve_page(name: str) -> FileResponse:
    path = os.path.join(FRONTEND_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"{name} not found.")
    return FileResponse(path)


@app.get("/")
def serve_index():
    return _serve_page("index.html")


@app.get("/genres")
def serve_genres():
    return _serve_page("genres.html")


@app.get("/artists")
def serve_artists():
    return _serve_page("artists.html")


@app.get("/reviews")
def serve_reviews():
    return _serve_page("reviews.html")


@app.get("/playlists")
def serve_playlists():
    return _serve_page("playlists.html")


@app.get("/purchase")
def serve_purchase():
    return _serve_page("purchases.html")


@app.get("/collection")
def serve_collection():
    return _serve_page("collection.html")


@app.get("/ask")
def serve_ask():
    return _serve_page("ask.html")


@app.get("/recent")
def serve_recent():
    return _serve_page("recent.html")


@app.get("/moodboard")
def serve_moodboard():
    return _serve_page("moodboard.html")


@app.get("/shows")
def serve_shows():
    return _serve_page("shows.html")
