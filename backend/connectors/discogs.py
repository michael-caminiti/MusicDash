import requests

from .base import BaseConnector

API_BASE = "https://api.discogs.com"


class DiscogsConnector(BaseConnector):
    """Live Discogs marketplace search (no wantlist) and collection management."""

    def __init__(self, token: str, username: str | None = None):
        self.username = username
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Discogs token={token}",
            "User-Agent": "MusicDash/0.1 +https://github.com/camin",
        })

    def search_release(self, artist: str, title: str) -> dict | None:
        resp = self.session.get(
            f"{API_BASE}/database/search",
            params={"artist": artist, "release_title": title, "type": "release", "per_page": 1},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        best = results[0]
        return {"release_id": best.get("id"), "thumb_url": best.get("thumb") or None}

    def search_releases(self, query: str, limit: int = 10) -> list:
        resp = self.session.get(
            f"{API_BASE}/database/search",
            params={"q": query, "type": "release", "per_page": limit},
        )
        resp.raise_for_status()
        return [
            {
                "release_id": r.get("id"),
                "thumb_url": r.get("thumb") or None,
                "title": r.get("title"),
                "year": r.get("year"),
            }
            for r in resp.json().get("results", [])
        ]

    def sync(self) -> dict:
        raise NotImplementedError("DiscogsConnector has no batch sync; use search_release per album.")

    def get_collection_folders(self) -> list:
        resp = self.session.get(f"{API_BASE}/users/{self.username}/collection/folders")
        resp.raise_for_status()
        return resp.json().get("folders", [])

    def get_collection_items(
        self, folder_id: int = 0, page: int = 1, per_page: int = 50,
        sort: str = "artist", sort_order: str = "asc",
    ) -> dict:
        resp = self.session.get(
            f"{API_BASE}/users/{self.username}/collection/folders/{folder_id}/releases",
            params={"page": page, "per_page": per_page, "sort": sort, "sort_order": sort_order},
        )
        resp.raise_for_status()
        data = resp.json()
        return {"items": data.get("releases", []), "pagination": data.get("pagination", {})}

    def rate_item(self, folder_id: int, release_id: int, instance_id: int, rating: int) -> None:
        # Discogs silently ignores `rating: 0` on the instance endpoint (it only accepts 1-5);
        # clearing a rating requires DELETE on the separate per-release rating resource instead.
        if rating == 0:
            resp = self.session.delete(f"{API_BASE}/releases/{release_id}/rating/{self.username}")
        else:
            resp = self.session.post(
                f"{API_BASE}/users/{self.username}/collection/folders/{folder_id}"
                f"/releases/{release_id}/instances/{instance_id}",
                json={"rating": rating},
            )
        resp.raise_for_status()

    def move_item(self, folder_id: int, release_id: int, instance_id: int, destination_folder_id: int) -> None:
        resp = self.session.post(
            f"{API_BASE}/users/{self.username}/collection/folders/{folder_id}"
            f"/releases/{release_id}/instances/{instance_id}",
            json={"folder_id": destination_folder_id},
        )
        resp.raise_for_status()

    def remove_item(self, folder_id: int, release_id: int, instance_id: int) -> None:
        resp = self.session.delete(
            f"{API_BASE}/users/{self.username}/collection/folders/{folder_id}"
            f"/releases/{release_id}/instances/{instance_id}",
        )
        resp.raise_for_status()

    def add_item(self, folder_id: int, release_id: int) -> dict:
        resp = self.session.post(
            f"{API_BASE}/users/{self.username}/collection/folders/{folder_id}/releases/{release_id}",
        )
        resp.raise_for_status()
        return resp.json()
