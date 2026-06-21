# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.
It's scoped to MusicDash specifically — the user's broader machine-wide CLAUDE.md covers their other
unrelated projects and isn't relevant here.

## What this project is

A personal music-taste dashboard. Python/FastAPI/SQLite backend, static HTML/JS frontend (no build
step). Ingests Last.fm scrobbles, Spotify playlists/library, and a Discogs collection, and adds a
Claude/Groq-backed layer on top: natural-language Q&A over listening history, playlist-idea generation,
genre primers, and an independent "second opinion" review of auto-built playlists. See `README.md` for
the full architecture, service list, and setup instructions — this file is about *working on the code*,
not running it.

## Key commands

```powershell
venv\Scripts\activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8001
# Dashboard at http://localhost:8001
```

`--reload` only watches `.py` files — it does **not** pick up `.env` changes. After editing `.env`,
either restart uvicorn manually or `touch backend/main.py` to force a reload.

## Architecture notes worth knowing before editing

- `backend/ingest.py` is the file-based ingestion path: reads `Documents/Music Discovery/` (taste
  profiles, Claude/ChatGPT review markdown, Last.fm CSV exports, Spotify playlist CSV/zip exports) and
  upserts into SQLite. It's idempotent and safe to re-run (`POST /api/refresh`). Every sub-ingest
  function no-ops cleanly if its expected subfolder doesn't exist yet — don't add guards that assume
  the folder structure is always present.
- `backend/connectors/` holds the live API clients (Spotify, Last.fm, Discogs, Anthropic LLM, Groq,
  Wikipedia) — these hit the network on every call, nothing is cached unless a table explicitly says so
  (`artist_images`, `genre_primers`, both TTL'd).
- `backend/main.py` is the single FastAPI app — all routes, all connector wiring, all business logic for
  playlist-idea generation/regeneration/audit live here. It's a big file; grep for the route you need
  rather than reading top to bottom.
- The MusicDash-specific Claude Code skills this project's workflow depends on live in
  `.claude/skills/` in *this repo* (`music-review`, `music-pulse`) — see below.

## Spotify API gotchas (confirmed live, don't re-discover these)

- **`GET /playlists/{id}/tracks` and `POST/DELETE` on the same old endpoint are all 403** as of
  Spotify's Feb 2026 platform change — even with valid OAuth scopes. Replaced by `/items` variants with
  a different payload shape (`item` key instead of `track`; removal requires
  `{"items": [{"uri": ...}]}`, not `{"uris": [...]}` or the old `{"tracks": [...]}`). `spotipy` hasn't
  caught up, so `backend/connectors/spotify.py` calls these directly via `requests` instead of through
  `spotipy`'s playlist methods. Don't assume any `spotipy` playlist read/write call still works without
  checking this file's comments first.
- **Playlist creation also moved**: `POST /v1/users/{user_id}/playlists` (spotipy's
  `user_playlist_create()`) is 403; use `POST /v1/me/playlists` instead.
- **Playlist name cap is exactly 200 characters** (undocumented; 201+ fails with a generic 400). See
  `SpotifyConnector.PLAYLIST_NAME_MAX`.
- **`/v1/search`'s real `limit` cap is 10**, not the documented 50 — `limit=11`+ fails with a generic
  400 `Invalid limit`. See `SpotifyConnector.SEARCH_LIMIT_MAX`.
- **`/artists/{id}/top-tracks`, `/audio-features`, `/recommendations`, `/related-artists` are all
  403/locked** under this app's quota mode. No usable replacement found — don't plan features around
  audio-feature filtering or artist top-tracks without re-checking quota access first.
- Track objects from `/v1/search` no longer include a `popularity` field for this app — a
  "prefer popular/obscure tracks" feature isn't buildable without that data right now.

## Other gotchas

- **Last.fm CSV exports have a UTF-8 BOM** — open with `encoding="utf-8-sig"`, not `"utf-8"`.
- **`python-dotenv`'s `load_dotenv()` doesn't override pre-existing OS environment variables** unless
  called with `override=True` (already set in `main.py`) — a stray OS-level env var can silently shadow
  a real `.env` value otherwise.
- **Discogs collection rating: `rating: 0` does not clear a rating** — that field only accepts 1-5. To
  actually clear one, call `DELETE /releases/{release_id}/rating/{username}` instead.
- **The Groq second-opinion gate is intentionally flag-only, never auto-remove.** An earlier auto-remove
  version was too trigger-happy on legitimate, simply-unfamiliar artists — see
  `_run_second_opinion_review` in `main.py` and `GroqConnector.REVIEW_SYSTEM_PROMPT` before changing this
  behavior.
- **The Purchase and Collection tabs deliberately don't cache Discogs pricing/stats** — Discogs's own
  marketplace numbers were found unreliable in past sessions, so staleness wasn't worth the speedup.

## Claude Code skills in this repo

- **`/music-review`** (`.claude/skills/music-review/SKILL.md`) — weekly/monthly taste-profile
  synthesis using live Spotify/Last.fm/Discogs MCP data, writes to `Documents/Music Discovery/Taste
  Profiles/` and `Claude Reviews/`, which MusicDash's `ingest.py` then reads. Also has a lightweight
  "daily mode" for conversational song-sorting into discovery playlists — no files written.
- **`/music-pulse`** (`.claude/skills/music-pulse/SKILL.md`) — ad-hoc/daily behavior-change check
  (binges, rotation breadth, genre/mood drift, time-of-day shift, new artists), writes a terse dated log
  to `Documents/Music Discovery/Daily Pulse/`. Strictly read-only against anything `/music-review` owns.

Both skills require the `spotify`, `lastfm`, and/or `discogs` MCP servers to be configured in the user's
Claude Code MCP settings (`spotify` and `lastfm` are personal forks/servers outside this repo;
`discogs` runs via `npx -y discogs-mcp-server`) — they are not part of this repo and won't be available
to a fresh checkout without that setup.

## Secrets

Never commit `.env`, `.cache`, or `.spotify_cache` — all three are gitignored and contain live API
keys/OAuth tokens. `.env.example` documents every variable the app reads; keep it in sync when adding a
new one.
