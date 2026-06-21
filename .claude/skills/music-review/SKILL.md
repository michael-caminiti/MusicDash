---
name: music-review
description: Runs the daily/weekly/monthly Music Discovery System review using live Spotify, Last.fm, and Discogs MCP data instead of manual CSV uploads to ChatGPT. Use when the user asks to run a music review, check listening taste, do the weekly/monthly music ritual, or invokes /music-review.
---

# Music Discovery Review

Claude is the curator layer for Michael's Music Discovery System, replacing ChatGPT. Data comes live from the `spotify`, `lastfm`, and `discogs` MCP servers — no CSV exports or uploads needed for the daily/weekly cadence.

Discogs username: `ojaivalley`. The `discogs` MCP server can search the database, check release pricing/availability (`num_for_sale`/`lowest_price` on `get_release`), and manage the wantlist — but it cannot complete a purchase (Discogs checkout is web-only). Treat it as "surface what's available and what it costs," never as "buy this."

Root folder: `%USERPROFILE%\Documents\Music Discovery` with subfolders `Spotify Playlists` (+ `Archive Zips`), `Last.fm Exports`, `Claude Reviews`, `Taste Profiles`.

Key discovery playlists referenced throughout: `2026 Finds`, `Interesting, Not Sure Yet`, `Repeat Candidates`, `Studio Inspiration`, `Genre Field Trips`, `Albums To Hear`, `Do Not Feed The Algorithm`.

Run this skill in one of three modes based on what the user asks for: **daily**, **weekly**, or **monthly**. Default to weekly if unclear and the user hasn't run it in the last 5+ days; ask if genuinely ambiguous.

## Daily mode

Lightweight, conversational — no files written.

1. Call `lastfm.get_now_playing` and/or `lastfm.get_recent_tracks` (small limit) to see what's been playing.
2. If the user describes a song that caught their ear, apply the sorting rule table below and tell them which playlist to use — then offer to add it via the `spotify` MCP tools if they confirm.

| When this happens | Put the song here |
|---|---|
| Immediate yes, want more like this | `2026 Finds` |
| Interesting, not sure yet | `Interesting, Not Sure Yet` |
| Want to hear it again before deciding | `Repeat Candidates` |
| Cool production/texture/drum/bass/synth/sample idea | `Studio Inspiration` |
| Belongs to the current genre exploration | `Genre Field Trips` |
| Want to come back to the full album | `Albums To Hear` |
| Useful reference but not representative of taste | `Do Not Feed The Algorithm` |

Rules: Likes are sacred — only use Spotify Likes for high-confidence songs, never for "merely pleasant." Uncertainty gets a playlist, not a Like. Don't manually log daily listening — Last.fm already does that.

## Weekly mode

1. **Pull live data** — no Exportify needed for this cadence:
   - `lastfm.get_recent_tracks(limit=200)`
   - `lastfm.get_top_artists(period="7day")` and `lastfm.get_top_tracks(period="7day")`
   - `spotify.SpotifyTopItems(item_type="tracks", time_range="short_term")` and `item_type="artists"` — Spotify's own ~4-week listening signal, independent of Last.fm's scrobble feed. Treat agreement between this and Last.fm's 7-day data as a stronger signal, and divergence as worth calling out (one source may be catching something the other misses, or one may be noise).
   - For the top 3-5 artists from `get_top_artists`: `lastfm.get_similar_artists` and `lastfm.get_artist_top_tags` — grounds genre/bridge-artist analysis in real similarity data instead of LLM guesswork. For 1-2 standout tracks: `lastfm.get_similar_tracks` to seed the "songs that stretch taste" list with real candidates rather than invented ones.
   - `spotify` tools: read current contents of `2026 Finds`, `Interesting, Not Sure Yet`, `Studio Inspiration`, `Genre Field Trips`.
2. **Read** `Documents\Music Discovery\Taste Profiles\CURRENT.md` for prior context (skip if it doesn't exist yet — first run).
3. **Analyze** the pulled data and answer, grounded in what you actually retrieved (don't hallucinate genres/artists, tags, or similarity matches not present in the data):
   1. What genres/subgenres show up this week, per `get_artist_top_tags` (not just impression/guesswork)
   2. What sonic traits repeat across songs
   3. What artists act as bridges between genres — look for overlaps in `get_similar_artists` results across this week's top artists
   4. Which playlist additions look like real taste signals vs. algorithmic noise
   5. What to explore next — prioritize `get_similar_artists` results not already in the taste profile or playlists
   6. What to avoid feeding the algorithm
   7. 3 playlist concepts based on this taste profile
   8. 10 songs that stretch taste without snapping it — draw from `get_similar_tracks` candidates where available, filtered by judgment, not pure invention
   9. One recommended genre field trip for next week
   10. Where Spotify's `SpotifyTopItems` (short_term) and Last.fm's 7-day data agree vs. diverge, and what that implies
4. **Write** the dated review to `Documents\Music Discovery\Claude Reviews\<YYYY-MM-DD> - Claude - Weekly Music Review.md` (create the folder if missing). Structure it like the prior `ChatGPT Reviews` template: top artists/tracks, songs that stood out, songs that didn't land, notes, and the 10 answers above.
5. **Update the living taste profile** at `Taste Profiles\CURRENT.md` — merge in new signal rather than just appending (rewrite sections as your understanding evolves), then also save a dated snapshot copy `Taste Profiles\<YYYY-MM-DD> - Taste Profile.md` for history. **Formatting matters here**: MusicDash's `ingest.py` parser expects `## 5. Things To Avoid` and `## 6. Playlist Ideas` to be markdown bullet lists (`- item` per line) — prose paragraphs in those two sections parse as empty and render as "none" on the Current Taste tab. Other sections (signals, genres, defining artists, next field trip, monthly notes) can stay as prose. **Also**: in each `Playlist Ideas` description, write real artist names capitalized (`Delta Sleep`, `Hotline TNT`) and genre/trait words lowercase (`post-rock`, `angular guitar interplay`) — MusicDash's "Create on Spotify" button classifies each comma-separated term by capitalization to decide whether to seed from that artist directly or search by genre, so an artist name written lowercase (or a genre word capitalized) will get misclassified and searched the wrong way.
6. **Optional playlist action**: if a track in `Interesting, Not Sure Yet` clearly reads as a confirmed taste signal from this week's data, propose moving it to `2026 Finds` via the `spotify` write tools — ask for explicit confirmation before making the change, since it mutates the user's library.
7. **Optional Discogs check**: for this week's #1 artist by playcount (or any artist just promoted to `2026 Finds` in step 6), run `discogs.search(artist=..., type="release")` and `discogs.get_release` on a relevant pressing to check `num_for_sale`/`lowest_price`. Cross-reference against `discogs.get_user_wantlist(username="ojaivalley")` — if a release is available, reasonably priced, and not already wantlisted, propose adding it via `discogs.add_to_wantlist` with explicit confirmation first. Skip silently (don't clutter the review) if nothing's for sale or nothing stands out — this is a bonus surfacing step, not a mandatory report section.

## Monthly mode

1. Do everything weekly mode does, but pull `lastfm.get_top_artists`/`get_top_tracks` with `period="12month"` and `period="overall"` too, plus `spotify.SpotifyTopItems` with `time_range="long_term"`, and diff against the previous month's `Taste Profiles\CURRENT.md` content (read it before overwriting) to identify what's stable vs. what's shifting. Run `get_similar_artists`/`get_artist_top_tags` against the longer-horizon top artists too — a genre/tag that holds up across both the 7-day and 12-month+long_term views is a much stronger durable signal than one that only shows up in a single week.
2. Answer the monthly question set: what stayed consistent, what changed, which genres are becoming stronger signals, which artists/songs define the current taste model, what to stop feeding the algorithm, what to explore next month, what playlist cleanup to do, and what (if anything) is worth saving as a durable high-level preference.
3. Remind the user to manually run the Exportify "Export All" backup (open the `Open Exportify.url` shortcut, choose Export All) and then the `Organize Exportify Downloads` shortcut — this is the one step that still requires a manual browser action since Exportify has no API/MCP path. This keeps a portable offline ZIP archive in `Spotify Playlists\Archive Zips` as a safety net.
4. Apply the durable-memory rule when updating `CURRENT.md`: only stable, high-level preferences belong there (e.g. "prefers atmospheric, melodic, emotionally rich music with textural production"). Never save individual song ratings or one-off listens.
5. Suggest concrete playlist cleanup actions (move winners from `Interesting, Not Sure Yet` to `2026 Finds`, archive the completed Genre Field Trip, start the next one) and offer to execute the moves via `spotify` write tools with confirmation.
6. **Discogs wantlist review**: pull `discogs.get_user_wantlist(username="ojaivalley")` in full and cross-reference against this month's durable-signal artists and the `Do Not Feed The Algorithm` playlist — flag any wantlist items whose artist has since drifted into "avoid" territory as cleanup candidates (propose removal, don't remove automatically). Also run the Discogs availability check (same as weekly step 7) against the month's strongest 2-3 durable artists, not just the single top one, since this cadence is about durable signal rather than one-week noise.

## Notes

- If the `spotify`, `lastfm`, or `discogs` MCP tools aren't available, tell the user clearly rather than fabricating playlist/listening/marketplace data.
- Discogs cannot complete purchases via API — never imply a purchase happened. All Discogs wantlist mutations require explicit confirmation first, same as Spotify playlist mutations.
- All playlist-mutating Spotify actions (adding/removing tracks, creating playlists) require explicit user confirmation first — these aren't easily reversible from chat.
