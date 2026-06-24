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

## Post-show mode

Lightweight and conversational, like daily mode — triggered by "I just saw {artist}" or similar. Writes one dated file; does **not** touch `CURRENT.md` directly (that stays the weekly/monthly job, same boundary as everywhere else in this system).

1. Ask 2-3 quick questions: what stood out (songs, banter, surprises), anything new worth following up on (opener, a cover, a deep cut that hit different live), and whether the show confirmed existing taste or pulled toward something new.
2. Write a dated entry to `Claude Reviews/<YYYY-MM-DD> - Post-Show - <Artist>.md` with the date, venue (if mentioned), artist, the user's answers, and a one-line signal-strength note — live discovery is a stronger signal than algorithmic discovery, so say explicitly if this looks like it should bump the artist toward "defining artist" status.
3. Don't edit `CURRENT.md` directly. The next weekly/monthly review's existing data-audit step (see Monthly mode step 2) will naturally pick up a post-show artist if it's backed by real sustained playcount — this entry just leaves a breadcrumb explaining *why* it mattered, for that future review to read.
4. If a song mentioned doesn't already have a home in a discovery playlist, apply the daily-mode sorting table above and offer to add it.

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
5. **Update the living taste profile** at `Taste Profiles\CURRENT.md` — merge in new signal rather than just appending (rewrite sections as your understanding evolves), then also save a dated snapshot copy `Taste Profiles\<YYYY-MM-DD> - Taste Profile.md` for history. **Formatting matters here** — `ingest.py` parses each `## N. <Header>` section differently depending on which bucket it falls in (see `TASTE_PROFILE_SECTIONS`/`LIST_SECTIONS`/`BULLET_SECTIONS`/`PROSE_SECTIONS` in `backend/ingest.py` if unsure):
   - **`## 2. Strong / Primary Genres`, `## 3. Emerging / Adjacent Genres To Test`, and `## 4. Artists That Define The Current Taste` are plain comma-separated lists** (`_parse_list_section` does a naive `.split(",")` over the whole section body) — one flat line of `Item One, Item Two, Item Three`. **No prose, no parentheticals, no markdown bold, no sub-bullets, no explanatory asides** — anything beyond bare comma-separated terms gets chopped into garbled chip fragments on the Current Taste tab. This is a real incident from 2026-06-24: writing "Indie rock, Jangle pop / lo-fi indie rock, ... Post-rock / post-metal (emerging). Dropped (zero support in real tag data): Chillwave, Vaporwave..." into Section 2 rendered as a row of broken/truncated chips. Put any explanatory context (why something was added/dropped, confidence level, grouping by thread) into `## 8. Monthly Tracking Notes` instead, which *is* prose.
   - `## 5. Things To Avoid` and `## 6. Playlist Ideas` must be markdown bullet lists (`- item` per line) — prose paragraphs there parse as empty and render as "none."
   - `## 1. Current Taste Signals`, `## 7. Next Field Trip Genre`, and `## 8. Monthly Tracking Notes` are the only sections that can be prose.
   **Also**: in each `Playlist Ideas` description, write real artist names capitalized (`Delta Sleep`, `Hotline TNT`) and genre/trait words lowercase (`post-rock`, `angular guitar interplay`) — MusicDash's "Create on Spotify" button classifies each comma-separated term by capitalization to decide whether to seed from that artist directly or search by genre, so an artist name written lowercase (or a genre word capitalized) will get misclassified and searched the wrong way.
6. **Optional playlist action**: if a track in `Interesting, Not Sure Yet` clearly reads as a confirmed taste signal from this week's data, propose moving it to `2026 Finds` via the `spotify` write tools — ask for explicit confirmation before making the change, since it mutates the user's library.
7. **Optional Discogs check**: for this week's #1 artist by playcount (or any artist just promoted to `2026 Finds` in step 6), run `discogs.search(artist=..., type="release")` and `discogs.get_release` on a relevant pressing to check `num_for_sale`/`lowest_price`. Cross-reference against `discogs.get_user_wantlist(username="ojaivalley")` — if a release is available, reasonably priced, and not already wantlisted, propose adding it via `discogs.add_to_wantlist` with explicit confirmation first. Skip silently (don't clutter the review) if nothing's for sale or nothing stands out — this is a bonus surfacing step, not a mandatory report section.

## Monthly mode

1. Do everything weekly mode does, but pull `lastfm.get_top_artists`/`get_top_tracks` with `period="12month"` and `period="overall"` too, plus `spotify.SpotifyTopItems` with `time_range="long_term"`, and diff against the previous month's `Taste Profiles\CURRENT.md` content (read it before overwriting) to identify what's stable vs. what's shifting. Run `get_similar_artists`/`get_artist_top_tags` against the longer-horizon top artists too — a genre/tag that holds up across both the 7-day and 12-month+long_term views is a much stronger durable signal than one that only shows up in a single week.
2. **Audit existing `CURRENT.md` content against this month's `get_top_artists(period="overall")` results (top 50).** Every artist named in Sections 1, 2, and 4 must be traceable to real data — cross-check each one against the overall top-50 list. Any artist with zero plays there is unverified content (inherited from an old template, a prior LLM guess, or stale carryover) and must be flagged and removed, not preserved by default. This caught a real incident on 2026-06-24: the "Artists That Define The Current Taste" section had been carrying 19 artists (Slowdive, Beach House, Boards of Canada, Massive Attack, Tycho, Tame Impala, etc.) copied verbatim from the original `000 - Starter Music Taste Profile.md` with zero scrobbles behind any of them — for over a month, across multiple reviews, because no review step ever checked the *existing* content against data, only added *new* content on top of it. Run this audit every monthly review, not just when the user happens to notice and flag it.
3. Answer the monthly question set: what stayed consistent, what changed, which genres are becoming stronger signals, which artists/songs define the current taste model, what to stop feeding the algorithm, what to explore next month, what playlist cleanup to do, and what (if anything) is worth saving as a durable high-level preference.
4. Remind the user to manually run the Exportify "Export All" backup (open the `Open Exportify.url` shortcut, choose Export All) and then the `Organize Exportify Downloads` shortcut — this is the one step that still requires a manual browser action since Exportify has no API/MCP path. This keeps a portable offline ZIP archive in `Spotify Playlists\Archive Zips` as a safety net.
5. Apply the durable-memory rule when updating `CURRENT.md`: only stable, high-level preferences belong there (e.g. "prefers atmospheric, melodic, emotionally rich music with textural production"). Never save individual song ratings or one-off listens.
6. Suggest concrete playlist cleanup actions (move winners from `Interesting, Not Sure Yet` to `2026 Finds`, archive the completed Genre Field Trip, start the next one) and offer to execute the moves via `spotify` write tools with confirmation.
7. **Discogs wantlist review**: pull `discogs.get_user_wantlist(username="ojaivalley")` in full and cross-reference against this month's durable-signal artists and the `Do Not Feed The Algorithm` playlist — flag any wantlist items whose artist has since drifted into "avoid" territory as cleanup candidates (propose removal, don't remove automatically). Also run the Discogs availability check (same as weekly step 7) against the month's strongest 2-3 durable artists, not just the single top one, since this cadence is about durable signal rather than one-week noise.

## Notes

- **Never let an artist or genre sit in `CURRENT.md` Sections 1, 2, or 4 unless it traces to a real data pull** (`get_top_artists`, `get_artist_top_tags`, `SpotifyTopItems`) from some run of this skill. Don't assume inherited content — from an older snapshot, a starter template, or a prior LLM pass — is correct just because it's already there. If you can't trace it, say so and propose removing it rather than silently keeping it.
- If the `spotify`, `lastfm`, or `discogs` MCP tools aren't available, tell the user clearly rather than fabricating playlist/listening/marketplace data.
- Discogs cannot complete purchases via API — never imply a purchase happened. All Discogs wantlist mutations require explicit confirmation first, same as Spotify playlist mutations.
- All playlist-mutating Spotify actions (adding/removing tracks, creating playlists) require explicit user confirmation first — these aren't easily reversible from chat.
