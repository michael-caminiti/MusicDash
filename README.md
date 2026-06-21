# MusicDash

A personal music-taste dashboard that turns Last.fm scrobbles, Spotify playlists/library, and a Discogs
collection into one place to see what you actually listen to, ask questions about your own history in
plain English, and act on it — spin up Spotify playlists from taste signals, get genre primers, check
what to buy next, and manage a vinyl/CD collection — all from a single local web app.

It grew out of a manual ritual of uploading Last.fm/Spotify CSV exports to ChatGPT for a weekly "what
does my taste look like" writeup. MusicDash replaces the manual export/upload step with live API
connectors and a SQLite cache, and replaces the ChatGPT writeup with a Claude Code skill
(`/music-review`, in a separate personal dotfiles/skills repo) that talks to the same data live.

## What it is, concretely

A single FastAPI process serving both a JSON API and a handful of static HTML/JS pages (no frontend
build step — open a page, it calls the API directly). Backed by one SQLite file. Designed to run
locally on one machine for one person; there's no multi-user concept anywhere in the schema.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3, FastAPI, Uvicorn |
| Storage | SQLite (single file, `backend/dashboard.db`, gitignored) |
| Frontend | Static HTML + vanilla JS + Chart.js (no React/Vue/build step) |
| LLM (taste Q&A, playlist ideas, genre primers) | Anthropic Claude (`anthropic` SDK) |
| LLM (second-opinion playlist review) | Groq free tier, Llama 3.3 70B (`openai`-SDK-compatible client) |
| External data | Last.fm API, Spotify Web API (`spotipy` + raw `requests` for newer endpoints), Discogs API, Wikipedia REST API |

## Services it talks to

- **Last.fm** — `user.getrecenttracks`, top artists/tracks, similar artists, artist/track top tags. Read-only, API-key auth. Source of truth for "what did I actually listen to."
- **Spotify** — OAuth (Authorization Code flow, token cached in `.spotify_cache`/`.cache`, gitignored). Reads playlists, top items, search; writes playlists (create, add/remove tracks) for the playlist-idea and field-trip features. Several endpoints are called directly via `requests` instead of `spotipy` because Spotify deprecated a handful of playlist read/write endpoints in Feb 2026 and `spotipy` hasn't caught up (`backend/connectors/spotify.py` documents each one).
- **Discogs** — collection (folders/items/ratings/moves), search, wantlist pricing/availability. Read/write for the Collection tab; read-only elsewhere. No purchase automation — Discogs checkout is web-only.
- **Anthropic (Claude)** — natural-language Q&A over scrobble history, new playlist-idea generation, distilling messy "genre note" prose into a clean playlist brief, genre history/key-artist/sonic-signature primers.
- **Groq (Llama 3.3 70B, free tier)** — an independent "second opinion" reviewer that flags (never auto-removes) tracks added to a field-trip playlist that don't clearly fit, after Claude's own pipeline picks them.
- **Wikipedia** (REST summary + legacy search API) — grounds genre primers in a real article when one exists; falls back to an LLM-only primer (clearly labeled `source: "llm"`) when Wikipedia has nothing.

## Data flow

- **File ingestion** (`backend/ingest.py`, idempotent, safe to re-run): reads `Documents/Music Discovery/` — taste-profile markdown (current + dated snapshots), weekly Claude/ChatGPT review markdown, Last.fm CSV exports, Spotify playlist CSV/zip exports — and upserts into SQLite. This folder is also the one place a separate Claude Code skill (`music-review`) writes to; MusicDash only ever reads it.
- **Live connectors** (`backend/connectors/`): hit Last.fm/Spotify/Discogs/Anthropic/Groq/Wikipedia directly on request; nothing here is cached except where a table explicitly says so (artist images, genre primers — both with a TTL).
- The **Purchase** and **Collection** tabs deliberately do *not* cache Discogs pricing/stats — past sessions found Discogs's own numbers unreliable enough that staleness wasn't worth the speedup.

## How to interact with it

```powershell
cd MusicDash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in real credentials, see below
uvicorn backend.main:app --reload --port 8001
```

Then open `http://localhost:8001`. The first run auto-creates `backend/dashboard.db` and ingests
whatever's currently in `Documents/Music Discovery/`.

### `.env`

See `.env.example` for the full list. Roughly:

- `DISCOGS_PERSONAL_ACCESS_TOKEN`, `DISCOGS_USERNAME`
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`
- `LASTFM_API_KEY`, `LASTFM_USER`
- `ANTHROPIC_API_KEY` — required for Ask, playlist-idea generation, genre primers
- `GROQ_API_KEY` — required for the second-opinion playlist gate (get a free key at console.groq.com)

Spotify auth is a one-time browser OAuth dance per machine: visiting any Spotify-backed feature without
a cached token returns a 401 with an `auth_url`; the frontend redirects you through Spotify's consent
screen and back to `/callback`, which stores the token locally.

### Pages

| Route | What it's for |
|---|---|
| `/` (Current Taste) | Latest taste-profile snapshot, strong/emerging genres, defining artists, playlist ideas (dismiss/restore/regenerate/generate-new), Next Field Trip Genre (with inline tracklist + "Try This"/"Try Again") |
| `/genres` | Genre trend chart across historical taste-profile snapshots |
| `/artists` | Top artists, all-time / 7-day / 30-day, with Spotify artist images |
| `/reviews` | Archive of past weekly/monthly review writeups (Claude or ChatGPT-sourced), empty templates filtered out |
| `/playlists` | Browse ingested Spotify playlist exports |
| `/purchase` | One row per album from the "2026 Finds" playlist — Discogs listing link + Amazon search link + Last.fm-similarity "you may also like" |
| `/collection` | Live read/write proxy to your real Discogs collection — folders, ratings, moves, add/remove, search-to-add |
| `/ask` | Free-text question answered against your real scrobble history (e.g. "did my listening change after X?") |
| `/recent` | Live Last.fm scrobbles in a filterable (today/24h/7d/30d) chronological list |

### API

Everything the frontend calls is also a plain JSON endpoint under `/api/...` — see `backend/main.py`
for the full route table (taste profile, genre primer, playlist ideas, ask, scrobbles, reviews,
playlists, purchases, collection, Spotify playlist-from-idea/from-genre-note + regenerate, playlist
audit). Nothing requires the browser; any endpoint can be curled directly for scripting.

### Claude Code skills (separate from this repo, but designed to work with it)

- `/music-review` — weekly/monthly taste-profile synthesis, writes to `Documents/Music Discovery/Taste Profiles/` and `Claude Reviews/`, which MusicDash then ingests.
- `/music-pulse` — lightweight ad-hoc/daily behavior-change check (binges, rotation breadth, genre/mood drift, time-of-day shift, new artists), writes a terse dated log to `Documents/Music Discovery/Daily Pulse/`. Doesn't touch anything `/music-review` owns and isn't ingested by MusicDash (yet — see Future Ideas).

## Future ideas / phases under consideration

Nothing below is scheduled — this is the running list of things that have come up as "could be cool"
during development, roughly in order of how concrete they are:

- **Ingest the Daily Pulse log into MusicDash.** Right now `/music-pulse` writes to a markdown file MusicDash never reads. A `/pulse` tab showing the recent entries (or a sparkline of volume/rotation-breadth over time) would close the loop.
- **Surface the playlist-strategy stats in the UI.** `_category_rejection_rates`/`/api/spotify/playlists/strategy-stats` already tracks which search categories (seed artist, similar artist, genre tag, free text) get rejected most over time and auto-disables bad ones — there's no frontend view of this yet, just the raw endpoint.
- **Surface the playlist audit in the UI.** `/api/spotify/playlists/audit` re-validates every previously-added track against current matching rules and flags drift, but today it's API-only — no page renders `flagged`/`count` for review.
- **A real "taste drift over time" visualization** beyond the current genre-trend chart — e.g. defining-artist turnover, or a timeline combining weekly snapshots with daily-pulse new-artist flags.
- **Discogs price-history tracking**, if a reliable enough source ever turns up — explicitly avoided so far because Discogs's own marketplace stats were found unreliable.
- **Second-opinion gate beyond field-trip playlists** — currently only `from-genre-note` playlists get the Groq review; regular playlist-idea creation/regenerate doesn't, mostly because field-trip genres are the riskiest (most LLM-distilled, least user-curated) case.
- **A second opinion *source*** beyond Groq/Llama, if quality ever becomes a problem again — kept cheap/free deliberately, revisit only if needed.
- **Smarter purchase recommendations** — currently a single-hop Last.fm similar-artist expansion off the "2026 Finds" playlist; could incorporate Discogs wantlist/collection signal or genre-primer data once it exists for more artists.
- **Audio-feature-based filtering** (e.g. prefer high-energy or low-valence tracks) — explicitly *not* currently buildable; Spotify's `/audio-features` and `/artists/{id}/top-tracks` are both locked behind extended-quota-mode restrictions for this app, so this is blocked until/unless that access changes.

## Known constraints worth knowing before changing things

- Single-user, single-machine by design — no auth layer, no multi-tenancy in the schema.
- Spotify search's real page-size cap is 10 (not the documented 50); several Spotify playlist read/write endpoints were silently deprecated in Feb 2026 in favor of new ones with different payload shapes — see the inline comments in `backend/connectors/spotify.py` before assuming `spotipy`'s methods still work.
- The Groq second-opinion gate is intentionally a flag-for-review step, not an auto-filter — an earlier auto-remove version was too trigger-happy on legitimate, simply-unfamiliar artists.
- `python-dotenv`'s `load_dotenv()` won't override pre-existing OS environment variables unless called with `override=True` (already done in `main.py`) — worth remembering if env vars ever seem to silently not take effect.
