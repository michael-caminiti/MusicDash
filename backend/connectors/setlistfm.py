import requests

from .base import BaseConnector

API_BASE = "https://api.setlist.fm/rest/1.0"


class SetlistFmConnector(BaseConnector):
    """Free, non-commercial-use setlist.fm API — used to seed pre-show playlists from real recent setlists."""

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Accept": "application/json"})

    def get_recent_setlists(self, artist_name: str, limit: int = 3) -> list:
        resp = self.session.get(
            f"{API_BASE}/search/setlists",
            params={"artistName": artist_name, "p": 1},
            timeout=10,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        setlists = resp.json().get("setlist", [])[:limit]
        results = []
        for s in setlists:
            songs = [
                song["name"]
                for set_ in s.get("sets", {}).get("set", [])
                for song in set_.get("song", [])
                if song.get("name")
            ]
            if not songs:
                continue
            results.append({
                "event_date": s.get("eventDate"),
                "venue": (s.get("venue") or {}).get("name"),
                "songs": songs,
            })
        return results

    def sync(self) -> dict:
        raise NotImplementedError("SetlistFmConnector has no batch sync; use get_recent_setlists per artist.")
