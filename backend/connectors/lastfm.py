import requests

from .base import BaseConnector

API_BASE = "https://ws.audioscrobbler.com/2.0/"


class LastfmConnector(BaseConnector):
    """Live Last.fm lookups for the Purchase tab's recommendation engine.

    Spotify's related-artist/recommendation endpoints are locked down for this
    app (403/404 as of Nov 2024's API changes), so Last.fm is the only working
    similarity signal available.
    """

    def __init__(self, api_key: str, user: str):
        self.api_key = api_key
        self.user = user

    def _get(self, method: str, **params) -> dict:
        resp = requests.get(API_BASE, params={
            "method": method, "api_key": self.api_key, "format": "json", **params,
        })
        resp.raise_for_status()
        return resp.json()

    def get_similar_artists(self, artist: str, limit: int = 10) -> list:
        data = self._get("artist.getsimilar", artist=artist, limit=limit)
        artists = data.get("similarartists", {}).get("artist", [])
        return [{"name": a["name"], "score": float(a.get("match", 0))} for a in artists]

    def get_weekly_top_artists(self, limit: int = 200) -> dict:
        data = self._get("user.gettopartists", user=self.user, period="7day", limit=limit)
        artists = data.get("topartists", {}).get("artist", [])
        return {a["name"].lower(): int(a.get("playcount", 0)) for a in artists}

    def get_recent_tracks(self, from_ts: int = None, limit: int = 200) -> list:
        """Live scrobble feed, most recent first. `from_ts` (unix seconds) bounds how far back to
        fetch; paginates since Last.fm caps each page at 200 and a busy day/week can exceed that."""
        tracks = []
        page = 1
        while True:
            params = {"user": self.user, "limit": limit, "page": page}
            if from_ts is not None:
                params["from"] = from_ts
            data = self._get("user.getrecenttracks", **params)
            recent = data.get("recenttracks", {})
            entries = recent.get("track", [])
            if isinstance(entries, dict):  # Last.fm returns a bare dict, not a list, for a single result
                entries = [entries]
            for e in entries:
                now_playing = e.get("@attr", {}).get("nowplaying") == "true"
                date_field = e.get("date", {})
                tracks.append({
                    "artist": e.get("artist", {}).get("#text", ""),
                    "track": e.get("name", ""),
                    "album": e.get("album", {}).get("#text", ""),
                    "now_playing": now_playing,
                    "uts": int(date_field["uts"]) if date_field.get("uts") else None,
                })
            attr = recent.get("@attr", {})
            if page >= int(attr.get("totalPages", 1)) or not entries:
                break
            page += 1
        return tracks

    def get_artist_top_tags(self, artist: str, limit: int = 10) -> list:
        data = self._get("artist.gettoptags", artist=artist)
        tags = data.get("toptags", {}).get("tag", [])
        ranked = sorted(tags, key=lambda t: int(t.get("count", 0)), reverse=True)
        return [t["name"] for t in ranked[:limit]]

    def sync(self) -> dict:
        raise NotImplementedError("LastfmConnector has no batch sync; use specific methods.")
