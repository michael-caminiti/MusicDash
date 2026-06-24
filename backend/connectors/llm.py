import json

import anthropic

ASK_MODEL = "claude-sonnet-4-6"
ASK_MAX_TOKENS = 800

ASK_SYSTEM_PROMPT = (
    "You answer questions about the user's real music listening history using only the scrobble data "
    "provided below (artist, track, played-at timestamp, most recent first). Be specific and cite real "
    "numbers — play counts, dates, artist names — drawn from the data rather than generic statements. "
    "If the data doesn't cover the time period the question asks about, say so plainly rather than "
    "guessing. Keep the answer conversational and a few short paragraphs at most."
)

GENERATE_IDEA_MODEL = ASK_MODEL
GENERATE_IDEA_MAX_TOKENS = 300

EXTRACT_IDEA_MODEL = ASK_MODEL
EXTRACT_IDEA_MAX_TOKENS = 200

EXTRACT_IDEA_SYSTEM_PROMPT = (
    'The text below is a freeform "next field trip genre" note — it may be a clean genre name, or a '
    "longer note mixing genre names, real artist names, and meta-commentary about how confident the "
    'recommendation is (ignore that meta-commentary entirely — phrases like "highest-confidence", '
    '"triple-confirmed", "get_similar_artists matches" describe the note\'s own methodology, not music).\n\n'
    'Respond with strict JSON only — no markdown fences, no extra text — in the shape '
    '{"title": "...", "description": "..."}.\n\n'
    "title: a short playlist name (under 50 characters) capturing the genre/vibe.\n"
    "description: a comma-or-colon-separated list of terms following this exact convention: every real "
    'artist name mentioned is capitalized (e.g. "Hotline TNT"), every genre/mood word is lowercase '
    '(e.g. "math rock"). Only include artists/genres actually named in the text.'
)

LINER_NOTES_MODEL = ASK_MODEL
LINER_NOTES_MAX_TOKENS = 500

LINER_NOTES_SYSTEM_PROMPT = (
    "Write liner notes for this playlist as if it were a real compilation/comp release — the kind of "
    "writeup you'd find on the back of a vinyl sleeve or a zine review. 2-3 short paragraphs: capture "
    "the mood/throughline connecting these tracks, reference a few of the actual artists by name, and "
    "use an evocative but not overwrought tone. No markdown, no headers — just the prose."
)

GENRE_PRIMER_MODEL = ASK_MODEL
GENRE_PRIMER_MAX_TOKENS = 600

GENRE_PRIMER_WITH_WIKI_SYSTEM_PROMPT = (
    'Given a music genre and a Wikipedia extract about it, respond with strict JSON only — no markdown '
    'fences, no extra text — in the shape {"key_artists": ["...", ...], "sonic_signatures": ["...", ...]}.\n\n'
    "key_artists: 5-8 real, important artists in this genre — prioritize ones relevant to someone who "
    "already likes the user's defining artists (given below), where there's a real fit.\n"
    "sonic_signatures: 4-6 short phrases describing the genre's actual sound (instrumentation, production "
    "traits, song structure, mood) — not history, just what it sounds like."
)

GENRE_PRIMER_NO_WIKI_SYSTEM_PROMPT = (
    'Wikipedia has no article for this genre. Using your own knowledge, respond with strict JSON only — '
    'no markdown fences, no extra text — in the shape '
    '{"history_text": "...", "key_artists": ["...", ...], "sonic_signatures": ["...", ...]}.\n\n'
    "history_text: 2-3 sentences on the genre's origin and context.\n"
    "key_artists: 5-8 real, important artists in this genre.\n"
    "sonic_signatures: 4-6 short phrases describing the genre's actual sound.\n"
    "If you aren't confident a genre by this name really exists, say so plainly in history_text instead "
    "of inventing details."
)

# The description format here has to match `_classify_idea_terms` in main.py exactly (capitalized =
# artist name, lowercase = genre/trait word) or the generated idea will silently search the wrong way
# once "Create on Spotify" runs against it.
GENERATE_IDEA_SYSTEM_PROMPT = (
    'You generate a single new Spotify playlist idea for this user based on their music taste profile. '
    'Respond with strict JSON only — no markdown fences, no extra text — in the shape '
    '{"title": "...", "description": "..."}.\n\n'
    "The description must be a comma-or-colon-separated list of terms, matching this exact convention: "
    'every artist name is capitalized (e.g. "Delta Sleep", "Hotline TNT"), every genre or trait word/phrase '
    'is lowercase (e.g. "post-rock", "angular, technical guitar interplay"). Mix a few specific real artist '
    'names with a few genre/mood descriptors, e.g.: "Delta Sleep, Hotline TNT, post-rock, angular, technical '
    'guitar interplay". Do not invent fictional artists.'
)


class LLMConnector:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def ask_about_history(self, question: str, scrobbles_text: str) -> str:
        response = self.client.messages.create(
            model=ASK_MODEL,
            max_tokens=ASK_MAX_TOKENS,
            system=ASK_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Scrobble history:\n{scrobbles_text}\n\nQuestion: {question}",
            }],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def generate_playlist_idea(self, taste_context: str, existing_titles: list) -> dict:
        existing = "\n".join(f"- {t}" for t in existing_titles) or "(none yet)"
        response = self.client.messages.create(
            model=GENERATE_IDEA_MODEL,
            max_tokens=GENERATE_IDEA_MAX_TOKENS,
            system=GENERATE_IDEA_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Taste profile:\n{taste_context}\n\n"
                    f"Existing idea titles — do not repeat or closely resemble any of these:\n{existing}"
                ),
            }],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return self._parse_json_response(text)

    def extract_idea_from_text(self, text: str) -> dict:
        response = self.client.messages.create(
            model=EXTRACT_IDEA_MODEL,
            max_tokens=EXTRACT_IDEA_MAX_TOKENS,
            system=EXTRACT_IDEA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        out = "".join(block.text for block in response.content if block.type == "text")
        return self._parse_json_response(out)

    def get_genre_primer_from_wiki(self, genre: str, wiki_extract: str, defining_artists: list) -> dict:
        response = self.client.messages.create(
            model=GENRE_PRIMER_MODEL,
            max_tokens=GENRE_PRIMER_MAX_TOKENS,
            system=GENRE_PRIMER_WITH_WIKI_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Genre: {genre}\n\nWikipedia extract:\n{wiki_extract}\n\n"
                    f"User's defining artists: {', '.join(defining_artists) or 'none'}"
                ),
            }],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return self._parse_json_response(text)

    def get_genre_primer_without_wiki(self, genre: str, defining_artists: list) -> dict:
        response = self.client.messages.create(
            model=GENRE_PRIMER_MODEL,
            max_tokens=GENRE_PRIMER_MAX_TOKENS,
            system=GENRE_PRIMER_NO_WIKI_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Genre: {genre}\n\nUser's defining artists: {', '.join(defining_artists) or 'none'}",
            }],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return self._parse_json_response(text)

    def generate_liner_notes(self, title: str, description: str, tracks: list) -> str:
        track_lines = "\n".join(f"- {t['name']} — {t['artist']}" for t in tracks) or "(no tracks yet)"
        response = self.client.messages.create(
            model=LINER_NOTES_MODEL,
            max_tokens=LINER_NOTES_MAX_TOKENS,
            system=LINER_NOTES_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Playlist: {title}\nConcept: {description}\n\nTracklist:\n{track_lines}",
            }],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
