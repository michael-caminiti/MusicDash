import json

from openai import OpenAI

from .base import BaseConnector

REVIEW_MODEL = "llama-3.3-70b-versatile"
REVIEW_MAX_TOKENS = 800

REVIEW_SYSTEM_PROMPT = (
    "You are reviewing candidate tracks for a Spotify playlist as a second, independent opinion — the "
    "playlist's title and description describe its intended genre/vibe. Default to trusting the curator "
    "who picked these — only flag a track if you have a *specific, concrete* reason it doesn't fit (e.g. "
    "you know this artist's actual genre and it's clearly unrelated). Not recognizing an artist, or being "
    "merely unsure, is NOT a reason to flag it — abstain in that case. Respond with strict JSON only — no "
    'markdown fences, no extra text — in the shape {"flagged": [{"uri": "...", "reason": "..."}, ...]}, '
    "where reason is one concrete sentence citing the actual conflict. Leave the list empty if you have no "
    "specific objections — an empty list is the expected, normal outcome, not a failure to find something."
)


class GroqConnector(BaseConnector):
    """Second-opinion gate for playlist candidates — a different model/vendor (Llama via Groq's free
    tier) than the Anthropic-based pipeline that builds them, so it can catch cases that pipeline's own
    genre/tag heuristics miss. Groq's API is OpenAI-compatible, hence reusing the `openai` SDK here.

    Flags rather than auto-rejects: a first pass that let this auto-remove tracks turned out to be
    overly trigger-happy (vetoed legitimate, simply-unfamiliar artists despite the prompt telling it not
    to) — surfacing flags with a reason for manual review is safer than trusting it unsupervised."""

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    def review_tracks(self, title: str, description: str, tracks: list) -> list:
        track_lines = "\n".join(f"{t['uri']} | {t['name']} — {t['artist']}" for t in tracks)
        response = self.client.chat.completions.create(
            model=REVIEW_MODEL,
            max_tokens=REVIEW_MAX_TOKENS,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Playlist: {title}\nDescription: {description}\n\nCandidate tracks:\n{track_lines}",
                },
            ],
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text).get("flagged", [])
