import requests

from .base import BaseConnector

API_BASE = "https://app.ticketmaster.com/discovery/v2"
GEOCODE_URL = "https://api.zippopotam.us/us"


class TicketmasterConnector(BaseConnector):
    """Live Ticketmaster Discovery API lookups — free self-serve key, no special approval needed.

    Ticketmaster's own `postalCode` + `radius` geo search is unreliable (confirmed live: returns zero
    results even for a real show in the exact postal code, e.g. a Turnstile date in Portland, ME with
    `postalCode=04106` and `radius=300` — that show *is* in the database, keyword-only search without a
    geo filter finds it fine). `latlong` + `radius` works correctly, so this connector geocodes the
    postal code once via the free Zippopotam.us API (no key needed) and searches by lat/long instead.
    """

    def __init__(self, api_key: str, postal_code: str):
        self.api_key = api_key
        self.latlong = self._geocode(postal_code)

    @staticmethod
    def _geocode(postal_code: str) -> str:
        resp = requests.get(f"{GEOCODE_URL}/{postal_code}", timeout=10)
        resp.raise_for_status()
        place = resp.json()["places"][0]
        return f"{place['latitude']},{place['longitude']}"

    def search_events_for_artist(self, artist_name: str, radius_miles: int = 100) -> list:
        resp = requests.get(
            f"{API_BASE}/events.json",
            params={
                "apikey": self.api_key,
                "keyword": artist_name,
                "latlong": self.latlong,
                "radius": radius_miles,
                "unit": "miles",
                "sort": "date,asc",
            },
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("_embedded", {}).get("events", [])
        results = []
        artist_lower = artist_name.lower()
        for e in events:
            # Ticketmaster's keyword search matches loosely against event titles/descriptions, not just
            # performer names — confirmed live: searching "Home" (a real band) also matched "Home Free",
            # "Comics Come Home", etc. with zero actual relation. `_embedded.attractions` lists the real
            # performers, so only keep events where the artist is actually one of them.
            attractions = e.get("_embedded", {}).get("attractions") or []
            if not any(artist_lower == a.get("name", "").lower() for a in attractions):
                continue
            venue = (e.get("_embedded", {}).get("venues") or [{}])[0]
            results.append({
                "event_name": e.get("name"),
                "event_date": (e.get("dates", {}).get("start", {}) or {}).get("localDate"),
                "venue": venue.get("name"),
                "city": (venue.get("city") or {}).get("name"),
                "url": e.get("url"),
                "ticketmaster_id": e.get("id"),
            })
        return results

    def sync(self) -> dict:
        raise NotImplementedError("TicketmasterConnector has no batch sync; use search_events_for_artist per artist.")
