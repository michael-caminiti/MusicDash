import os

import requests
import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from .base import BaseConnector

SCOPES = "playlist-modify-public playlist-modify-private playlist-read-private"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".spotify_cache")


class SpotifyConnector(BaseConnector):
    """Live Spotify Web API access for MusicDash (separate from the spotify-mcp server)."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.oauth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_handler=CacheFileHandler(cache_path=CACHE_PATH),
            open_browser=False,
        )
        self._public = spotipy.Spotify(
            client_credentials_manager=SpotifyClientCredentials(
                client_id=client_id, client_secret=client_secret
            )
        )

    def is_authorized(self) -> bool:
        """True if we have a usable token — refreshing an expired access token via the stored
        refresh_token counts as authorized. Access tokens expire hourly; without this, the app would
        wrongly tell you to reconnect Spotify every hour even though the refresh_token is still good."""
        token = self.oauth.cache_handler.get_cached_token()
        if not token:
            return False
        if self.oauth.is_token_expired(token):
            try:
                self.oauth.refresh_access_token(token["refresh_token"])
            except Exception:
                return False
        return True

    def get_auth_url(self) -> str:
        return self.oauth.get_authorize_url()

    def handle_callback(self, code: str) -> None:
        self.oauth.get_access_token(code, as_dict=False)

    def _authed_client(self) -> spotipy.Spotify:
        token = self.oauth.cache_handler.get_cached_token()
        if not token:
            raise RuntimeError("Spotify is not connected yet.")
        if self.oauth.is_token_expired(token):
            token = self.oauth.refresh_access_token(token["refresh_token"])
        return spotipy.Spotify(auth=token["access_token"])

    def get_artist_image(self, name: str) -> dict | None:
        results = self._public.search(q=name, type="artist", limit=1)
        items = results.get("artists", {}).get("items", [])
        if not items:
            return None
        artist = items[0]
        images = artist.get("images") or []
        return {
            "spotify_id": artist["id"],
            "image_url": images[-1]["url"] if images else None,
        }

    def _auth_headers(self) -> dict:
        token = self.oauth.cache_handler.get_cached_token()
        if not token:
            raise RuntimeError("Spotify is not connected yet.")
        if self.oauth.is_token_expired(token):
            token = self.oauth.refresh_access_token(token["refresh_token"])
        return {"Authorization": f"Bearer {token['access_token']}"}

    def get_playlist_albums(self, playlist_id: str) -> list:
        """Distinct (artist, album) pairs found in a playlist, with album art and an example track.

        Spotify requires a logged-in user token to read playlist contents (client-credentials gets a
        403 even for public playlists). As of Spotify's Feb 2026 API change, the old `/tracks` endpoint
        (what spotipy's playlist_items() calls) 403s outright and was replaced by `/items` — spotipy
        doesn't support the new endpoint yet, so this calls it directly.
        """
        headers = self._auth_headers()
        albums = {}
        offset = 0
        while True:
            resp = requests.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                headers=headers,
                params={"offset": offset, "limit": 100},
            )
            resp.raise_for_status()
            page = resp.json()
            items = page.get("items", [])
            for entry in items:
                track = entry.get("item")
                if not track or track.get("type") != "track" or not track.get("album"):
                    continue
                album = track["album"]
                if not album.get("artists"):
                    continue
                artist = album["artists"][0]["name"]
                key = (artist, album["name"])
                added_at = entry.get("added_at")
                if key not in albums:
                    images = album.get("images") or []
                    albums[key] = {
                        "artist": artist,
                        "album": album["name"],
                        "spotify_album_id": album.get("id"),
                        "image_url": images[0]["url"] if images else None,
                        "example_track": track.get("name"),
                        "added_at": added_at,
                    }
                elif added_at and (not albums[key]["added_at"] or added_at > albums[key]["added_at"]):
                    # Multiple tracks can map to the same album and aren't necessarily in
                    # chronological order — keep the latest add date so "added recently" means
                    # "at least one track from this album was added recently."
                    albums[key]["added_at"] = added_at
            offset += len(items)
            if offset >= page.get("total", 0) or not items:
                break
        return list(albums.values())

    # /v1/search's real limit cap is 10 as of 2026 (despite docs still saying 50) — values above
    # 10 fail outright with a 400 "Invalid limit", confirmed by direct testing against the live API.
    SEARCH_LIMIT_MAX = 10

    def search_tracks_by_artist(self, artist_name: str, limit: int) -> list:
        """Tracks for a confirmed artist match, or [] if no result is actually that artist.

        `/artists/{id}/top-tracks` is 403/locked for this app (same Nov 2024 extended-quota-mode
        restriction as `/recommendations` and `/related-artists`, confirmed live), so this uses the
        `artist:` search field filter instead. That filter is unreliable on its own — even when the
        top hit is correct, later results can drift to a completely different artist (a real query for
        "CHON" returned "Bubble Dream" by Chon correctly at position 1, then "Dance Hall Days" by Wang
        Chung at position 2, confirmed live) — so every item is checked individually, not just the
        first. Also handles short/uncommon names where even the top hit misses (e.g. "Som" returns
        the unrelated artist "sombr" first) by simply returning nothing in that case.
        """
        sp = self._authed_client()
        limit = min(limit, self.SEARCH_LIMIT_MAX)
        results = sp.search(q=f'artist:"{artist_name}"', type="track", limit=limit)
        items = results.get("tracks", {}).get("items", [])
        return [t for t in items if t["artists"][0]["name"].lower() == artist_name.lower()]

    def search_track_by_title_and_artist(self, title: str, artist_name: str) -> dict | None:
        """Match a specific song title to a specific artist — used for setlist-to-Spotify matching.

        `track:"..."` alone is too loose for common song titles (confirmed live: searching a generic
        setlist song title without an artist filter returned tracks by unrelated artists, not the one
        actually playing it), so this combines `track:` and `artist:` filters and still verifies the
        returned artist name matches exactly, same defensive check as `search_tracks_by_artist`.
        """
        sp = self._authed_client()
        results = sp.search(q=f'track:"{title}" artist:"{artist_name}"', type="track", limit=5)
        items = results.get("tracks", {}).get("items", [])
        matches = [t for t in items if t["artists"][0]["name"].lower() == artist_name.lower()]
        return matches[0] if matches else None

    def search_tracks_by_genre(self, term: str, limit: int) -> list:
        """Tracks tagged with this genre via Spotify's `genre:` field filter.

        Confirmed live this returns zero off-topic noise (unlike free-text search) but is a much
        narrower, more obscure tagging system that misses well-known genre-defining artists — use
        alongside `search_tracks_freetext`, not as a sole source.
        """
        sp = self._authed_client()
        limit = min(limit, self.SEARCH_LIMIT_MAX)
        results = sp.search(q=f'genre:"{term}"', type="track", limit=limit)
        return results.get("tracks", {}).get("items", [])

    def search_tracks_freetext(self, term: str, limit: int) -> list:
        sp = self._authed_client()
        limit = min(limit, self.SEARCH_LIMIT_MAX)
        results = sp.search(q=term, type="track", limit=limit)
        return results.get("tracks", {}).get("items", [])

    def get_user_playlists(self) -> list:
        sp = self._authed_client()
        playlists = []
        results = sp.current_user_playlists(limit=50)
        while results:
            playlists.extend({"id": p["id"], "name": p["name"]} for p in results["items"])
            results = sp.next(results) if results.get("next") else None
        return playlists

    def get_playlist_track_uris(self, playlist_id: str) -> set:
        """Just the track URIs in a playlist, for dedup checks. Uses `/items` — see `get_playlist_albums`."""
        headers = self._auth_headers()
        uris = set()
        offset = 0
        while True:
            resp = requests.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                headers=headers,
                params={"offset": offset, "limit": 100},
            )
            resp.raise_for_status()
            page = resp.json()
            items = page.get("items", [])
            for entry in items:
                track = entry.get("item")
                if track and track.get("uri"):
                    uris.add(track["uri"])
            offset += len(items)
            if offset >= page.get("total", 0) or not items:
                break
        return uris

    # Undocumented — found by bisecting live against the real API: 200-char names succeed, 201+ fail
    # with a generic 400 "too long playlist name" error.
    PLAYLIST_NAME_MAX = 200

    def get_playlist_tracks(self, playlist_id: str) -> list:
        """Track name/artist/uri in playlist order, for displaying a tracklist inline. Same pagination
        as `get_playlist_track_uris` but keeps names instead of just collapsing to a set of URIs."""
        headers = self._auth_headers()
        tracks = []
        offset = 0
        while True:
            resp = requests.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                headers=headers,
                params={"offset": offset, "limit": 100},
            )
            resp.raise_for_status()
            page = resp.json()
            items = page.get("items", [])
            for entry in items:
                track = entry.get("item")
                if track and track.get("uri"):
                    tracks.append({
                        "uri": track["uri"], "name": track.get("name", ""),
                        "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                    })
            offset += len(items)
            if offset >= page.get("total", 0) or not items:
                break
        return tracks

    def create_playlist(self, title: str, description: str) -> dict:
        """`POST /v1/users/{user_id}/playlists` (spotipy's `user_playlist_create()`) 403s now even with
        valid playlist-modify scopes, confirmed live — replaced by `POST /v1/me/playlists`."""
        headers = self._auth_headers()
        resp = requests.post(
            "https://api.spotify.com/v1/me/playlists",
            headers=headers,
            json={"name": title[:self.PLAYLIST_NAME_MAX], "public": False, "description": description[:300]},
        )
        resp.raise_for_status()
        return resp.json()

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: list) -> None:
        """`POST /playlists/{id}/tracks` (spotipy's `playlist_add_items()`) 403s now, confirmed live —
        replaced by `POST /playlists/{id}/items`."""
        if not track_uris:
            return
        headers = self._auth_headers()
        resp = requests.post(
            f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
            headers=headers,
            json={"uris": track_uris},
        )
        resp.raise_for_status()

    def remove_tracks_from_playlist(self, playlist_id: str, track_uris: list) -> None:
        """`DELETE /playlists/{id}/tracks` is 403 too, confirmed live. The replacement,
        `DELETE /playlists/{id}/items`, also rejects the add-endpoint's `{"uris": [...]}` shape and the
        old remove shape `{"tracks": [{"uri": ...}]}` with a confusing `400 "No uris provided"` either
        way — the only shape that actually works is `{"items": [{"uri": ...}]}`, found by trial and
        error against the live API."""
        if not track_uris:
            return
        headers = self._auth_headers()
        resp = requests.delete(
            f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
            headers=headers,
            json={"items": [{"uri": u} for u in track_uris]},
        )
        resp.raise_for_status()

    def sync(self) -> dict:
        raise NotImplementedError("SpotifyConnector has no batch sync; use specific methods.")
