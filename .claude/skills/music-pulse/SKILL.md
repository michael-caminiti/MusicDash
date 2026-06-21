---
name: music-pulse
description: Lightweight, ad-hoc/daily check-in on listening *behavior changes* — binge patterns, rotation breadth, genre/mood drift, time-of-day shift, first-time-heard artists. Distinct from /music-review's weekly/monthly taste-profile synthesis. Use when the user wants a quick pulse check, asks "what's changed in my listening lately," or invokes /music-pulse.
---

# Music Pulse Check

A fast, comparative behavior check — not a taste-profile rewrite. `/music-review` (weekly/monthly) answers "what does my taste look like now"; this answers "what changed recently, and is it a real shift or noise." Run this as often as the user likes — daily, every few days, or purely ad hoc.

Never touch `Taste Profiles\CURRENT.md`, playlist ideas, or anything else `/music-review` owns — this skill is read-only against that file (for context only) and write-only to its own folder below.

## 1. Determine the comparison window

Default: last 24 hours vs. the 24 hours before that. If the user names a different window ("this week vs last week", "since Tuesday"), use that instead. If the user references something specific (an event, a new person, a mood), frame the comparison around that instead of a flat day-split if it makes the analysis sharper.

## 2. Pull live data

- `lastfm.get_recent_tracks` for both the current and prior period (use `from`/`to` if the tool supports bounding; otherwise pull a generous limit and split by timestamp yourself).
- `lastfm.get_now_playing` if useful context.
- Read `Documents\Music Discovery\Taste Profiles\CURRENT.md` (skip silently if missing) — only to know the existing `defining_artists` list, so you can tell genuinely new artists apart from already-known ones. Don't quote or rewrite this file.

## 3. Run the comparison checklist

Only include a finding if the data actually supports it — don't manufacture a narrative.

1. **Volume** — plays per day in each period. A meaningful jump or drop is worth noting; normalize for period length if they differ.
2. **Rotation breadth** — distinct artists per period (and per play, to normalize). Narrowing = binge mode; widening = exploration mode.
3. **Top artists/tracks this period** — what dominates, and whether it's new or a returning favorite.
4. **First-time-heard artists** — anyone in this period not in `CURRENT.md`'s `defining_artists` and not a near-miss spelling of one. Call these out explicitly; they're the most useful daily-cadence signal for catching things before the weekly review would.
5. **Binge concentration** — flag if one artist/track accounts for a disproportionate share of the period's plays (e.g. one artist >25-30% of plays).
6. **Genre/mood shift** — `lastfm.get_artist_top_tags` on each period's top 3-5 artists; compare tag profiles. Only call this a "shift" if the tag sets are genuinely different, not just the same genre worded differently.
7. **Time-of-day shift** — bucket plays into night/morning/afternoon/evening for each period and compare the distribution.

## 4. Give a short, direct readout

Lead with whatever's actually notable — don't force all seven checklist items into the answer if most are flat. If everything's stable, say that plainly in a sentence or two rather than padding. Cite real numbers, not vibes.

## 5. Write a dated note

Append (don't overwrite) to `Documents\Music Discovery\Daily Pulse\<YYYY-MM-DD> - Pulse.md` (create the folder/file if missing) — a short, timestamped section per run, since the user may run this more than once a day:

```
## <HH:MM> — <window compared>

<2-4 sentence summary of what actually changed, with numbers>
```

Keep entries terse — this is a running log to skim later, not a full report (that's what the dated entry's conversational answer in-chat already covered).

## Notes

- If `lastfm` MCP tools aren't available, say so plainly rather than fabricating listening data.
- This is a read-only-against-taste-profile, observational skill — it never proposes playlist moves, Discogs actions, or taste-profile edits. If something notable comes up that belongs in one of those (e.g. a clear new defining artist), mention it as a suggestion for the next `/music-review` run, not act on it directly.
