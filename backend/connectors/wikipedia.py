import requests

from .base import BaseConnector

SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
SEARCH_API = "https://en.wikipedia.org/w/api.php"

# Wikimedia's API rejects requests' default User-Agent (silent 403, no readable error body) — their API
# etiquette policy requires an identifying UA. Confirmed live: same request succeeds once this is set.
HEADERS = {"User-Agent": "MusicDash/1.0 (personal music dashboard; not a bot)"}


class WikipediaConnector(BaseConnector):
    """Read-only genre lookups for the 'Next Field Trip Genre' primer. No API key needed."""

    def get_genre_summary(self, genre: str) -> dict | None:
        title = self._resolve_title(genre)
        if not title:
            return None
        resp = requests.get(SUMMARY_API.format(title=title.replace(" ", "_")), headers=HEADERS)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("type") == "disambiguation":
            return None
        return {
            "title": data.get("title", genre),
            "extract": data.get("extract", ""),
            "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        }

    def _resolve_title(self, genre: str) -> str | None:
        """Wikipedia's summary endpoint needs an exact page title — genre names from the taste profile
        ("Math Rock") don't always match Wikipedia's casing/phrasing ("Math rock"), so search first."""
        resp = requests.get(SEARCH_API, params={
            "action": "query", "list": "search", "srsearch": genre,
            "srlimit": 1, "format": "json",
        }, headers=HEADERS)
        if resp.status_code != 200:
            return None
        results = resp.json().get("query", {}).get("search", [])
        return results[0]["title"] if results else None
