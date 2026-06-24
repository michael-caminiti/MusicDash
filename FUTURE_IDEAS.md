# Future Ideas

A running brainstorm pool — nothing here is scheduled or committed. Pull from this in future sessions
instead of re-brainstorming from scratch. Items already shipped are noted inline so this stays an
accurate "what's left" list, not a changelog.

**Already shipped** (for reference, not re-suggestion): tour radar (Ticketmaster), pre-show playlists
(setlist.fm), Songkick tracked-shows import, manual show entry + delete, post-show capture (skill),
Bandcamp embeds on the Purchase tab, Purchase tab recent/older split + already-owned filtering,
genre-primer starter-pack playlists, cached liner notes, conversational playlist requests in Ask,
cover-art mood board, one-track-per-artist enforcement across all playlist generation.

## Concert / Live Music

- **Festival lineup cross-reference** — when a Ticketmaster event is a festival, pull the full lineup and flag which other acts overlap with the taste profile/defining artists ("3 other artists you like are also playing this").
- **Ticket price / on-sale change alerts** — poll a tracked show's Ticketmaster price range/on-sale status and flag changes.
- **Outbound .ics export of the Shows tab** — mirror the Songkick *import* with an export, so a phone calendar can subscribe directly to MusicDash's own shows list.
- **Multi-city tour-date comparison** — for an artist with several upcoming dates, list all of them (not just the nearest) for travel-planning.
- **Opener taste-signal check** — when a show's billing includes openers, check Last.fm for whether they're already a taste signal and surface a "worth a pre-show listen" flag.
- **Show-attendance-to-taste-profile correlation** — Songkick's iCal feed can also return *past* attendance with a different filter param; cross-referencing against when an artist became a "defining artist" could show whether seeing someone live actually predicts taste shift.
- **Known limitations worth revisiting**: no cross-source dedup between Ticketmaster/Songkick/manual rows (a real show can appear twice); deleting a Ticketmaster- or Songkick-sourced row doesn't stop it reappearing on the next Refresh — a "dismiss permanently" flag (distinct from hard delete) would fix that properly.

## Physical Media / Collecting

- **Discogs new-arrivals digest by genre tag** — surface what's newly listed in genres from the taste profile, discovery from the supply side rather than just your own listening.
- **Pressing/condition comparison on the Purchase tab** — when multiple Discogs pressings exist for an album, surface first-pressing vs. reissue and price-history signal instead of just "View Listings."
- **Wantlist decay alerts** — flag wantlist items sitting 90+ days with zero `num_for_sale` as dead wants worth pruning.
- **Visual "shelf" view of the collection** — spine/cover view sorted by genre, color, or year.
- **Bandcamp wishlist/purchase-history sync** — deferred during the Bandcamp build as the most fragile of three options considered; would need a manually-extracted session cookie against undocumented endpoints. Revisit only if the manual-link/embed-player approach stops feeling sufficient.
- **Bandcamp tag-page new-release digest** — reuse the existing public `bcsearch_public_api` connector for genre-tag browsing, parallel to the Discogs new-arrivals idea above.

## Taste Intelligence / Analysis

- **Tag co-occurrence network visualization** — a real graph of how genre clusters merge/split over time (the shoegaze-emo bridge, made visual) instead of reading it in prose.
- **"Why did I like this" recommendation lineage** — reconstruct the seed → similar-artist → genre-tag chain that led to any given track, as a readable trace.
- **Listening-rut detector** — flag when the top-10 artists haven't changed in N weeks, paired with a forced field-trip suggestion.
- **Counter-programming generator** — a playlist of things maximally *distant* from the taste profile, for deliberate ear-stretching rather than gentle adjacency.
- **Genre-trend chart: confirmed vs. aspirational** — distinguish data-backed genre entries from carried-forward/unverified ones over time (a direct callback to the CURRENT.md audit incident from this session, where unverified entries sat unquestioned for a month).

## Generative / Creative

- **Liner notes as a print-ready insert card** — export the existing generated liner notes + tracklist as a single-page PDF/HTML mimicking a real sleeve insert.
- **Mood board genre-overlay clustering** — group the cover-art grid by genre tag with color as a secondary signal, not just raw hue sort.
- **Bridge-artist starter packs** — seed a starter-pack playlist from the cross-genre bridge artists identified in weekly reviews specifically, not just a single genre's key artists.
- **Genre field-trip "syllabus"** — combine the existing genre primer with a suggested listening order across its key artists.
- **Show-poster collage** — combine event art (Ticketmaster/Songkick images, where available) into a personal "shows I'm going to" poster grid, parallel to the mood board.

## Social / Shared Listening

- **Taste-overlap diff against a friend** — paste in another Last.fm username, get a real overlap/divergence report and shared bridge artists.
- **Shareable text-blurb export of a playlist idea** — one-click plain-text export (artist list + reasoning) for texting a friend, no Spotify link required.
- **Blind taste-test mode** — surface a track from a discovery playlist with metadata hidden, log the gut reaction, then reveal.
- **"Same show?" check** — cross-reference the Shows tab against a friend's Songkick feed (if shared) to see overlap.

## Rituals / Gamification

- **Discovery streak tracker** — count consecutive weeks with at least one track promoted into "2026 Finds."
- **Genre "passport"** — a stamp-collection view of every genre tag ever confirmed as a real signal, with first-discovered date.
- **Annual taste yearbook** — durable artists of the year, biggest field trip, weirdest one-day binge, auto-generated at year-end.
- **Annual "shows attended" recap** — parallel yearbook entry now that real show data flows through the Shows tab.

## Visualization

- **Listening-intensity calendar heatmap** — GitHub-contributions-style, color-coded by dominant genre cluster per day.
- **Bridge-artist constellation map** — force-directed graph of `get_similar_artists` overlaps across the full top-50, rendered as an actual star map.
- **Show venue pin map** — upcoming/past shows plotted geographically; real venue geo data already flows through both Ticketmaster and Songkick.

## Infrastructure / Quality-of-Life

- **Ingest the Daily Pulse log into MusicDash** — `/music-pulse` writes to a markdown file MusicDash never reads yet; a `/pulse` tab or sparkline would close the loop.
- **Surface playlist-strategy stats in the UI** — `_category_rejection_rates`/`/api/spotify/playlists/strategy-stats` already tracks rejection rates per search category; no frontend view exists yet.
- **Surface the playlist audit in the UI** — `/api/spotify/playlists/audit` re-validates tracks against current matching rules; today it's API-only.
- **Second-opinion gate beyond field-trip playlists** — currently only `from-genre-note` playlists get the Groq review.
- **Smarter purchase recommendations** — incorporate Discogs wantlist/collection signal, not just single-hop Last.fm similarity off "2026 Finds."
- **Audio-feature-based filtering** — explicitly *blocked*, not deprioritized: Spotify's `/audio-features` and `/artists/{id}/top-tracks` are locked behind extended-quota-mode restrictions for this app. Revisit only if that access ever changes.
- **Offline/static full-profile export** — a single shareable HTML snapshot of the whole taste profile + reviews + playlist ideas, no API access required to view.
- **Ask-tab voice mode** — local speech-to-text so daily-mode song-sorting and Ask-tab questions work hands-free.
