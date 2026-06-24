# MusicDash

A personal music-taste dashboard that turns Last.fm scrobbles, Spotify playlists/library, and a Discogs
collection into one place to see what you actually listen to, ask questions about your own history in
plain English, and act on it — spin up Spotify playlists from taste signals, get genre primers, check
what to buy next (with Bandcamp embeds you can actually listen to), manage a vinyl/CD collection, track
upcoming shows across Ticketmaster/Songkick in one place with pre-show playlists seeded from real
setlists, and generate liner notes for any playlist — all from a single local web app.

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
| External data | Last.fm API, Spotify Web API (`spotipy` + raw `requests` for newer endpoints), Discogs API, Wikipedia REST API, Bandcamp public search API, Ticketmaster Discovery API, setlist.fm API, Songkick (public iCal feed, no API) |
| Image processing (mood board) | Pillow |

## Services it talks to

- **Last.fm** — `user.getrecenttracks`, top artists/tracks, similar artists, artist/track top tags. Read-only, API-key auth. Source of truth for "what did I actually listen to."
- **Spotify** — OAuth (Authorization Code flow, token cached in `.spotify_cache`/`.cache`, gitignored). Reads playlists, top items, search; writes playlists (create, add/remove tracks) for the playlist-idea and field-trip features. Several endpoints are called directly via `requests` instead of `spotipy` because Spotify deprecated a handful of playlist read/write endpoints in Feb 2026 and `spotipy` hasn't caught up (`backend/connectors/spotify.py` documents each one).
- **Discogs** — collection (folders/items/ratings/moves), search, wantlist pricing/availability. Read/write for the Collection tab; read-only elsewhere. No purchase automation — Discogs checkout is web-only.
- **Anthropic (Claude)** — natural-language Q&A over scrobble history, new playlist-idea generation, distilling messy "genre note" prose into a clean playlist brief, genre history/key-artist/sonic-signature primers.
- **Groq (Llama 3.3 70B, free tier)** — an independent "second opinion" reviewer that flags (never auto-removes) tracks added to a field-trip playlist that don't clearly fit, after Claude's own pipeline picks them.
- **Wikipedia** (REST summary + legacy search API) — grounds genre primers in a real article when one exists; falls back to an LLM-only primer (clearly labeled `source: "llm"`) when Wikipedia has nothing.
- **Bandcamp** — read-only, no API key. Uses the same public JSON endpoint Bandcamp's own site search calls (`bcsearch_public_api`) to find an album/track's embed ID, then renders Bandcamp's official `EmbeddedPlayer` widget on the Purchase tab so you can actually listen, not just link out.
- **Ticketmaster Discovery API** — read-only, free self-serve key. Powers the Shows tab's tour radar: searches for upcoming events near you for your durable defining artists and anything currently in "2026 Finds."
- **setlist.fm** — read-only, free non-commercial key. Pulls an artist's recent real setlists to seed a pre-show playlist ranked by how often each song actually gets played live.
- **Songkick** — read-only, no API key (their key program is currently closed to new applicants). Imports your own tracked/attending shows via the public per-user iCal feed, independent of whatever MusicDash has detected as a taste signal. Bandsintown has no equivalent feed and requires artist/business approval for API access, so there's a manual-add fallback on the Shows tab for anything from there or any other source.

## Data flow

- **File ingestion** (`backend/ingest.py`, idempotent, safe to re-run): reads `Documents/Music Discovery/` — taste-profile markdown (current + dated snapshots), weekly Claude/ChatGPT review markdown, Last.fm CSV exports, Spotify playlist CSV/zip exports — and upserts into SQLite. This folder is also the one place a separate Claude Code skill (`music-review`) writes to; MusicDash only ever reads it.
- **Live connectors** (`backend/connectors/`): hit Last.fm/Spotify/Discogs/Anthropic/Groq/Wikipedia/Bandcamp/Ticketmaster/setlist.fm/Songkick directly on request; nothing here is cached except where a table explicitly says so (artist images and genre primers, both TTL'd; the Discogs collection cache and album color palette, both refreshed on demand rather than TTL'd; liner notes, cached indefinitely until explicitly regenerated).
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
- `ANTHROPIC_API_KEY` — required for Ask, playlist-idea generation, genre primers, liner notes
- `GROQ_API_KEY` — required for the second-opinion playlist gate (get a free key at console.groq.com)
- `TICKETMASTER_API_KEY`, `TICKETMASTER_LOCATION` — required for the Shows tab's tour radar (free key at developer.ticketmaster.com; `LOCATION` is your home postal/ZIP code)
- `SETLISTFM_API_KEY` — required for pre-show playlists (free non-commercial key at api.setlist.fm)
- `SONGKICK_USERNAME` — required to import your Songkick-tracked shows (no key needed, just your username)

Spotify auth is a one-time browser OAuth dance per machine: visiting any Spotify-backed feature without
a cached token returns a 401 with an `auth_url`; the frontend redirects you through Spotify's consent
screen and back to `/callback`, which stores the token locally.

### Pages

| Route | What it's for |
|---|---|
| `/` (Current Taste) | Latest taste-profile snapshot, strong/emerging genres, defining artists, playlist ideas (dismiss/restore/regenerate/generate-new, plus a per-idea Liner Notes button), Next Field Trip Genre (with inline tracklist, "Try This"/"Try Again", a genre primer via "Tell Me More", and a "Build Starter Pack Playlist" button seeded from the primer's key artists) |
| `/genres` | Genre trend chart across historical taste-profile snapshots |
| `/artists` | Top artists, all-time / 7-day / 30-day, with Spotify artist images |
| `/reviews` | Archive of past weekly/monthly review writeups (Claude or ChatGPT-sourced), empty templates filtered out |
| `/playlists` | Browse ingested Spotify playlist exports |
| `/purchase` | Albums from "2026 Finds" added in the last 7 days by default (older ones collapse behind "show older"), already-owned albums filtered out against your real Discogs collection — each row has an inline Bandcamp embed player where a match exists, plus Discogs/Bandcamp/Amazon links and Last.fm-similarity "you may also like" |
| `/collection` | Live read/write proxy to your real Discogs collection — folders, ratings, moves, add/remove, search-to-add |
| `/ask` | Free-text question answered against your real scrobble history (e.g. "did my listening change after X?") — or, if it reads as a playlist request ("make me a playlist of..."), renders a creatable playlist idea instead of a text answer |
| `/recent` | Live Last.fm scrobbles in a filterable (today/24h/7d/30d) chronological list |
| `/moodboard` | Cover-art grid from "2026 Finds," sorted by each album's dominant color |
| `/shows` | Tour radar (Ticketmaster, for your defining artists + "2026 Finds") merged with your Songkick-tracked shows and any manually-added ones; per-show status (new/interested/going/passed), pre-show playlists seeded from real recent setlists via setlist.fm, and delete |

### API

Everything the frontend calls is also a plain JSON endpoint under `/api/...` — see `backend/main.py`
for the full route table (taste profile, genre primer + starter-pack, playlist ideas, liner notes, ask,
scrobbles, reviews, playlists, purchases, mood board, collection, shows (refresh/status/manual/delete/
pre-show playlist), Spotify playlist-from-idea/from-genre-note + regenerate, playlist audit). Nothing
requires the browser; any endpoint can be curled directly for scripting.

### Claude Code skills (separate from this repo, but designed to work with it)

- `/music-review` — weekly/monthly taste-profile synthesis, writes to `Documents/Music Discovery/Taste Profiles/` and `Claude Reviews/`, which MusicDash then ingests. Also has a post-show capture mode — a quick "I just saw {artist}" conversation logs a dated entry without touching `CURRENT.md` directly (that stays the weekly/monthly job).
- `/music-pulse` — lightweight ad-hoc/daily behavior-change check (binges, rotation breadth, genre/mood drift, time-of-day shift, new artists), writes a terse dated log to `Documents/Music Discovery/Daily Pulse/`. Doesn't touch anything `/music-review` owns and isn't ingested by MusicDash (yet — see Future Ideas).

## Future ideas / phases under consideration

See [`FUTURE_IDEAS.md`](FUTURE_IDEAS.md) for the full running brainstorm pool (concert/live-music,
physical media, taste intelligence, generative/creative, social, rituals, visualization, and
infrastructure ideas) — nothing in it is scheduled.

## Known constraints worth knowing before changing things

- Single-user, single-machine by design — no auth layer, no multi-tenancy in the schema.
- Spotify search's real page-size cap is 10 (not the documented 50); several Spotify playlist read/write endpoints were silently deprecated in Feb 2026 in favor of new ones with different payload shapes — see the inline comments in `backend/connectors/spotify.py` before assuming `spotipy`'s methods still work.
- The Groq second-opinion gate is intentionally a flag-for-review step, not an auto-filter — an earlier auto-remove version was too trigger-happy on legitimate, simply-unfamiliar artists.
- `python-dotenv`'s `load_dotenv()` won't override pre-existing OS environment variables unless called with `override=True` (already done in `main.py`) — worth remembering if env vars ever seem to silently not take effect.
- Ticketmaster's `postalCode`+`radius` geo search is unreliable (confirmed live) — the Shows connector geocodes to `latlong` instead. Songkick's API key program is currently closed to new applicants and Bandsintown requires artist/business approval, so both are handled without a real API (a public iCal feed and a manual-add fallback, respectively). Full detail in `CLAUDE.md`.
- The Shows tab has no cross-source dedup — the same real show can appear once from Ticketmaster and once from Songkick. Deleting a Ticketmaster- or Songkick-sourced row only hides it locally; it can reappear on the next Refresh since there's no "dismiss permanently" flag yet.

## First-time setup guide (Windows, zero assumed setup)

This walks through getting MusicDash running from a completely blank Windows machine — no Python, no
Git, no accounts set up yet. It's long on purpose: every step is spelled out so nothing's assumed.
Budget about 30-45 minutes, most of it waiting on installers and signing up for free accounts.

You'll end up with the dashboard open in your browser at `http://localhost:8001`. A few tabs (Ask,
playlist ideas, genre primers) need extra API keys — those are clearly marked **optional** below, so
you can get the core dashboard running first and add them later if you want.

### Step 1 — Install Python

1. Go to **python.org/downloads** and click the big "Download Python" button (any 3.11 or newer version is fine).
2. Run the installer. **Important:** on the very first install screen, check the box at the bottom that says **"Add python.exe to PATH"** before clicking Install. This is the most commonly missed step and causes every later command to fail with "python is not recognized."
3. When it finishes, open **PowerShell** (click Start, type `PowerShell`, press Enter) and type:
   ```powershell
   python --version
   ```
   You should see something like `Python 3.12.x`. If you instead see an error, close PowerShell, reopen it, and try again (PATH changes sometimes need a fresh window) — if it still fails, redo the Python install and make sure that checkbox was checked.

### Step 2 — Install Git

1. Go to **git-scm.com/downloads** and download the Windows installer.
2. Run it — the default options on every screen are fine, just keep clicking "Next" then "Install."
3. Back in PowerShell, confirm it worked:
   ```powershell
   git --version
   ```

### Step 3 — Get the code

In PowerShell:
```powershell
cd ~
git clone https://github.com/michael-caminiti/musicdiscovery.git MusicDash
cd MusicDash
```
This downloads the project into a folder called `MusicDash` in your user folder. (If you weren't given
access to that GitHub repo yet, ask whoever shared this guide to add your GitHub account as a
collaborator — it's currently private.)

### Step 4 — Set up the Python environment

Still in PowerShell, inside the `MusicDash` folder:
```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
- The second line should make your prompt show `(venv)` at the start — that means it worked.
- The third line downloads everything the app needs; it can take a minute or two.
- **Every time you come back to work on this later**, you'll need to re-run `venv\Scripts\activate` first (but not the other two commands) — that's what tells PowerShell to use the right Python setup.

### Step 5 — Create your `.env` file (your personal settings/keys)

In File Explorer, go into the `MusicDash` folder, find the file named `.env.example`, copy it, and
rename the copy to exactly `.env` (no `.example` at the end — and make sure Explorer isn't hiding the
real file extension; if you're not sure, do this in PowerShell instead):
```powershell
copy .env.example .env
```
You'll fill this file in with real values as you go through the steps below — open it in Notepad
whenever a step says to add something:
```powershell
notepad .env
```

### Step 6 — Last.fm account + API key (required)

1. If you don't already have one, make a free account at **last.fm** and use it for a few days so it has some listening history (it tracks what you play if you connect it to Spotify/Apple Music/etc. — see Last.fm's own "Connect" page for that, it's separate from this app).
2. Go to **last.fm/api/account/create** and fill out the short form (any name/description is fine — this is just to get a key, not to publish an app).
3. After submitting, you'll see an **API key**. Copy it.
4. In your `.env` file, fill in:
   ```
   LASTFM_API_KEY=<paste the key here>
   LASTFM_USER=<your Last.fm username>
   ```

### Step 7 — Spotify Developer app (required)

1. Go to **developer.spotify.com/dashboard** and log in with your normal Spotify account.
2. Click **Create app**. Fill in any name/description. For **Redirect URI**, enter exactly:
   ```
   http://127.0.0.1:8001/callback
   ```
   (this has to match exactly, including `http://` not `https://` — Spotify will reject anything else later if it doesn't match).
3. Check the box agreeing to Spotify's developer terms, then click Save.
4. On the app's page, click **Settings** to find your **Client ID**, and click **View client secret** for the **Client Secret**.
5. In `.env`, fill in:
   ```
   SPOTIFY_CLIENT_ID=<your client id>
   SPOTIFY_CLIENT_SECRET=<your client secret>
   SPOTIFY_REDIRECT_URI=http://127.0.0.1:8001/callback
   ```

### Step 8 — Discogs account + token (required for the Collection/Purchase tabs)

1. Make a free account at **discogs.com** if you don't have one (and optionally add a few records to your collection there so the Collection tab has something to show).
2. Go to **discogs.com/settings/developers** and click **Generate new token**.
3. Copy the token shown.
4. In `.env`:
   ```
   DISCOGS_PERSONAL_ACCESS_TOKEN=<your token>
   DISCOGS_USERNAME=<your discogs username>
   ```

### Step 9 — Anthropic API key (optional — needed for the Ask tab, playlist-idea generation, genre primers)

1. Go to **console.anthropic.com**, sign up, and add a small amount of billing credit (a few dollars covers a lot of usage for personal use).
2. Go to **API Keys** in the left sidebar and click **Create Key**.
3. In `.env`:
   ```
   ANTHROPIC_API_KEY=<your key>
   ```
   If you'd rather skip this for now, just leave it out — every other tab still works fine, and the app will tell you clearly (not silently fail) if you click something that needs it.

### Step 10 — Groq API key (optional — only needed for the "second opinion" playlist check)

1. Go to **console.groq.com**, sign up (it's free, no billing needed for this).
2. Go to **API Keys** and create one.
3. In `.env`:
   ```
   GROQ_API_KEY=<your key>
   ```

### Step 11 — Shows tab keys (optional — only needed for tour radar, pre-show playlists, and Songkick import)

1. **Ticketmaster** (tour radar): go to **developer.ticketmaster.com**, sign up, create an app, and copy its **Consumer key**. In `.env`:
   ```
   TICKETMASTER_API_KEY=<your key>
   TICKETMASTER_LOCATION=<your home postal/ZIP code>
   ```
2. **setlist.fm** (pre-show playlists): go to **api.setlist.fm**, register for a free account, and apply for an API key (it's free for non-commercial use and arrives immediately). In `.env`:
   ```
   SETLISTFM_API_KEY=<your key>
   ```
3. **Songkick** (imports shows you already track there): no key needed — just your Songkick username. In `.env`:
   ```
   SONGKICK_USERNAME=<your songkick username>
   ```
   If you skip any of these, the Shows tab still works for the parts that don't need them — e.g. manually adding shows always works regardless.

### Step 12 — Run it

Back in PowerShell (make sure you see `(venv)` at the start of the line — if not, run `venv\Scripts\activate` again):
```powershell
uvicorn backend.main:app --reload --port 8001
```
Leave this window open — it's the running app. Open your browser and go to:
```
http://localhost:8001
```
You should see the MusicDash dashboard. To stop the app later, click into that PowerShell window and press `Ctrl+C`. To start it again next time, you only need Steps 4 (just the `activate` line) and 12.

### Step 13 — Connect Spotify

The first time you open a tab that needs Spotify (Purchase, Collection, or any playlist-creation
button), the app will show a link to connect your Spotify account. Click it, log into Spotify if
asked, click **Agree**, and you'll be redirected back to the dashboard, now connected. This only needs
to be done once per computer.

### You're done

At this point every tab should work except possibly Ask/playlist-generation/genre-primers if you
skipped Steps 9-10, or parts of the Shows tab if you skipped Step 11. If something doesn't work, the
error message in the page is meant to say exactly which `.env` value is missing or wrong — re-check
that value's step above.

**Optional, advanced:** the Current Taste, Genre Trends, and Reviews tabs are richest if you also have
a `Documents\Music Discovery` folder with `Taste Profiles\CURRENT.md`, `Claude Reviews\`, `Last.fm
Exports\`, and `Spotify Playlists\` subfolders — these come from a separate Claude Code skill
(`/music-review`) that a more technical user would set up. Without it, those three tabs just show "no
data yet" — every other tab works independently of this folder.
