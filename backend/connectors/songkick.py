import requests

from .base import BaseConnector

ICAL_URL = "https://www.songkick.com/users/{username}/calendars.ics"


class SongkickConnector(BaseConnector):
    """Songkick's API key program is currently closed to new applicants while they rework the API
    (confirmed live), so this reads the public per-user iCal feed of tracked shows instead — no key
    needed, just the username. Hand-parsed rather than pulling in the `icalendar` package: the feed's
    shape is simple and stable (verified live against a real account), and this project only adds a
    new dependency when one is actually load-bearing (see Pillow for the mood board)."""

    def __init__(self, username: str):
        self.username = username

    def get_tracked_events(self) -> list:
        resp = requests.get(
            ICAL_URL.format(username=self.username),
            params={"filter": "attendance"},
            timeout=10,
        )
        resp.raise_for_status()
        return self._parse_ics(resp.text)

    @staticmethod
    def _parse_ics(text: str) -> list:
        # RFC 5545 line unfolding: a line starting with a single space is a continuation of the
        # previous line.
        lines = text.replace("\r\n", "\n").split("\n")
        unfolded = []
        for line in lines:
            if line.startswith(" ") and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)

        events = []
        current = {}
        for line in unfolded:
            if line == "BEGIN:VEVENT":
                current = {}
            elif line == "END:VEVENT":
                if current:
                    events.append(current)
            elif ":" in line:
                key, _, value = line.partition(":")
                key = key.split(";")[0]
                current[key] = value.replace("\\,", ",").replace("\\n", " ").strip()

        results = []
        for e in events:
            summary = e.get("SUMMARY", "")
            artist = summary.split(" at ", 1)[0].strip() if " at " in summary else summary
            location_parts = [p.strip() for p in e.get("LOCATION", "").split(",")]
            venue = location_parts[0] if location_parts else None
            city = location_parts[3] if len(location_parts) > 3 else None
            dtstart = e.get("DTSTART", "")
            event_date = f"{dtstart[:4]}-{dtstart[4:6]}-{dtstart[6:8]}" if len(dtstart) >= 8 else None
            results.append({
                "artist": artist,
                "event_name": summary,
                "event_date": event_date,
                "venue": venue,
                "city": city,
                "url": e.get("URL"),
                "songkick_uid": e.get("UID"),
            })
        return results

    def sync(self) -> dict:
        raise NotImplementedError("SongkickConnector has no batch sync; use get_tracked_events.")
