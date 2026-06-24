import requests

from .base import BaseConnector

SEARCH_URL = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"
EMBEDDABLE_TYPES = {"a", "t"}  # album, track — bands ("b") and labels have no EmbeddedPlayer


class BandcampConnector(BaseConnector):
    """Stateless lookups against Bandcamp's public search-autocomplete API.

    No auth/session needed — this is the same JSON endpoint Bandcamp's own site search bar calls,
    not the bot-challenge-walled `/search` HTML page. Bandcamp has no public OAuth API for
    purchases/wishlist, so this connector only covers discovery (search + embed), not account data.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MusicDash/0.1 +https://github.com/camin"})

    def search_album(self, artist: str, album: str) -> dict | None:
        match = self._search(f"{artist} {album}", search_filter="a")
        if not match:
            match = self._search(f"{artist} {album}", search_filter="")
        if not match or match.get("item_type") not in EMBEDDABLE_TYPES:
            return None
        return match

    def _search(self, text: str, search_filter: str) -> dict | None:
        resp = self.session.post(
            SEARCH_URL,
            json={"search_text": text, "search_filter": search_filter, "full_page": False, "fan_id": None},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("auto", {}).get("results", [])
        if not results:
            return None
        best = results[0]
        return {
            "item_id": best.get("id"),
            "item_type": best.get("type"),
            "url": best.get("item_url_path"),
            "thumb_url": best.get("img"),
        }

    def embed_url(self, item_id: int, item_type: str) -> str:
        kind = "album" if item_type == "a" else "track"
        return (
            f"https://bandcamp.com/EmbeddedPlayer/{kind}={item_id}/size=small/"
            "bgcol=ffffff/linkcol=0687f5/tracklist=false/transparent=true/"
        )

    def sync(self) -> dict:
        raise NotImplementedError("BandcampConnector has no batch sync; use search_album per album.")
