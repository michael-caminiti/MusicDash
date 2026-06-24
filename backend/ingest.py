import csv
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone

from . import db

MUSIC_DISCOVERY_ROOT = os.getenv(
    "MUSIC_DISCOVERY_ROOT",
    os.path.expanduser("~/Documents/Music Discovery"),
)

# Maps known header text variants -> canonical section key.
TASTE_PROFILE_SECTIONS = {
    "current taste signals": "current_taste_signals",
    "strong genres": "strong_genres",
    "strong / primary genres": "strong_genres",
    "emerging genres": "emerging_genres",
    "emerging / adjacent genres to test": "emerging_genres",
    "artists that define the current taste": "defining_artists",
    "things to avoid": "things_to_avoid",
    "playlist ideas": "playlist_ideas",
    "next field trip genre": "next_field_trip_genre",
    "monthly tracking notes": "monthly_tracking_notes",
}

LIST_SECTIONS = {"strong_genres", "emerging_genres", "defining_artists"}
BULLET_SECTIONS = {"things_to_avoid", "playlist_ideas"}
PROSE_SECTIONS = {"current_taste_signals", "next_field_trip_genre", "monthly_tracking_notes"}

SKIP_TASTE_PROFILE_FILES = {"000 - Starter Music Taste Profile.md", "Current Music Taste Profile.md"}

HEADER_RE = re.compile(r"^#{1,2}\s*(?:\d+\.\s*)?(.+?)\s*$", re.MULTILINE)
REVIEW_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) - (ChatGPT|Claude) - Weekly Music Review\.md$")
SNAPSHOT_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) - Taste Profile\.md$")
LOOSE_PLAYLIST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) - Spotify - (.+)\.csv$")
ARCHIVE_ZIP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) - Spotify - .+\.zip$")

PLAYLIST_CSV_FIELDS = {
    "track_uri": "Track URI", "track_name": "Track Name", "artist_names": "Artist Name(s)",
    "album_name": "Album Name", "album_release_date": "Album Release Date",
    "duration_ms": "Track Duration (ms)", "popularity": "Popularity",
    "isrc": "ISRC", "added_at": "Added At",
}

# "1. Delta Sleep — 26 plays" or "1. **Delta Sleep** — 26 plays"
ARTIST_LINE_RE = re.compile(r"^\d+[\.\)]\s*\**([^—\-(]+?)\**\s*[—\-]\s*(\d+)\s*plays?", re.IGNORECASE)
# "6–9 (3 plays each): Greet Death, Saves the Day, Slow Pulp, Toledo"
ARTIST_RANGE_RE = re.compile(r"^\d+[–\-]\d+\s*\((\d+)\s*plays?\s*each\)\s*:\s*(.+)$", re.IGNORECASE)
# "1. "Heaven Year" — Bleary Eyed (12 plays)"
TRACK_LINE_RE = re.compile(r'^\d+[\.\)]\s*"?([^"]+?)"?\s*[—\-]\s*([^(]+?)\s*\((\d+)\s*plays?\)', re.IGNORECASE)
# "3–6 (3 plays each): "Old Soul," "Sofa Boy," ... — all Delta Sleep"
TRACK_RANGE_RE = re.compile(r"^\d+[–\-]\d+\s*\((\d+)\s*plays?\s*each\)\s*:\s*(.+?)\s*[—\-]\s*all\s+(.+)$", re.IGNORECASE)

PLACEHOLDER_BODIES = {"paste from last.fm here.", ""}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_stat(path: str):
    st = os.stat(path)
    return str(st.st_mtime), st.st_size


def _already_ingested(conn, path: str, mtime: str, size: int) -> bool:
    row = conn.execute(
        "SELECT file_mtime, file_size FROM ingest_log WHERE file_path = ?", (path,)
    ).fetchone()
    return row is not None and row["file_mtime"] == mtime and row["file_size"] == size


def _log_ingest(conn, path: str, mtime: str, size: int, rows_affected: int, status: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log (file_path, file_mtime, file_size, rows_affected, ingested_at, status)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_mtime=excluded.file_mtime, file_size=excluded.file_size,
            rows_affected=excluded.rows_affected, ingested_at=excluded.ingested_at, status=excluded.status
        """,
        (path, mtime, size, rows_affected, _now_iso(), status),
    )


def _split_sections(body: str) -> dict:
    """Split a markdown file's body into {header_text_lower: section_body} by ## headers."""
    matches = list(HEADER_RE.finditer(body))
    sections = {}
    for i, m in enumerate(matches):
        header = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[header] = body[start:end].strip()
    return sections


def _parse_list_section(text: str) -> list:
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_bullet_section(text: str) -> list:
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("-"):
            items.append(line.lstrip("-").strip())
    return items


def _parse_playlist_ideas(text: str) -> list:
    ideas = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        line = line.lstrip("-").strip()
        m = re.match(r"\*\*(.+?)\*\*\s*[—\-]\s*(.+)", line)
        if m:
            ideas.append({"title": m.group(1).strip(), "description": m.group(2).strip()})
        else:
            ideas.append({"title": line, "description": ""})
    return ideas


def _ingest_one_taste_profile(conn, path: str, snapshot_date: str) -> int:
    mtime, size = _file_stat(path)
    if _already_ingested(conn, path, mtime, size):
        return 0

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    sections = _split_sections(raw)
    canonical = {}
    for header, body in sections.items():
        key = TASTE_PROFILE_SECTIONS.get(header)
        if key:
            canonical[key] = body

    schema_matched = 1 if canonical else 0
    is_empty_template = 1 if schema_matched and not any(canonical.values()) else 0

    strong_genres = json.dumps(_parse_list_section(canonical.get("strong_genres", "")))
    emerging_genres = json.dumps(_parse_list_section(canonical.get("emerging_genres", "")))
    defining_artists = json.dumps(_parse_list_section(canonical.get("defining_artists", "")))
    things_to_avoid = json.dumps(_parse_bullet_section(canonical.get("things_to_avoid", "")))
    playlist_ideas = json.dumps(_parse_playlist_ideas(canonical.get("playlist_ideas", "")))

    conn.execute(
        """
        INSERT INTO taste_profile_snapshots (
            snapshot_date, source_path, current_taste_signals, strong_genres_json,
            emerging_genres_json, defining_artists_json, things_to_avoid_json,
            playlist_ideas_json, next_field_trip_genre, monthly_tracking_notes,
            raw_body, schema_matched, is_empty_template, file_mtime, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            source_path=excluded.source_path,
            current_taste_signals=excluded.current_taste_signals,
            strong_genres_json=excluded.strong_genres_json,
            emerging_genres_json=excluded.emerging_genres_json,
            defining_artists_json=excluded.defining_artists_json,
            things_to_avoid_json=excluded.things_to_avoid_json,
            playlist_ideas_json=excluded.playlist_ideas_json,
            next_field_trip_genre=excluded.next_field_trip_genre,
            monthly_tracking_notes=excluded.monthly_tracking_notes,
            raw_body=excluded.raw_body,
            schema_matched=excluded.schema_matched,
            is_empty_template=excluded.is_empty_template,
            file_mtime=excluded.file_mtime,
            ingested_at=excluded.ingested_at
        """,
        (
            snapshot_date, path, canonical.get("current_taste_signals"), strong_genres,
            emerging_genres, defining_artists, things_to_avoid, playlist_ideas,
            canonical.get("next_field_trip_genre"), canonical.get("monthly_tracking_notes"),
            raw, schema_matched, is_empty_template, mtime, _now_iso(),
        ),
    )

    status = "empty_template" if is_empty_template else ("ok" if schema_matched else "unstructured_legacy")
    _log_ingest(conn, path, mtime, size, 1, status)
    return 1


def _ingest_taste_profiles(conn, root: str, stats: dict) -> None:
    folder = os.path.join(root, "Taste Profiles")
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if not name.endswith(".md") or name in SKIP_TASTE_PROFILE_FILES:
            continue
        path = os.path.join(folder, name)
        if name == "CURRENT.md":
            snapshot_date = "CURRENT"
        else:
            m = SNAPSHOT_FILENAME_RE.match(name)
            if not m:
                continue
            snapshot_date = m.group(1)
        stats["taste_profiles"] += _ingest_one_taste_profile(conn, path, snapshot_date)


def _parse_top_artists(text: str) -> list:
    artists = []
    rank = 1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = ARTIST_RANGE_RE.match(line)
        if m:
            plays = int(m.group(1))
            for name in m.group(2).split(","):
                name = name.strip()
                if name:
                    artists.append({"rank": rank, "name": name, "plays": plays})
                    rank += 1
            continue
        m = ARTIST_LINE_RE.match(line)
        if m:
            artists.append({"rank": rank, "name": m.group(1).strip(), "plays": int(m.group(2))})
            rank += 1
    return artists


def _parse_top_tracks(text: str) -> list:
    tracks = []
    rank = 1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = TRACK_RANGE_RE.match(line)
        if m:
            plays = int(m.group(1))
            artist = m.group(3).strip()
            titles = re.findall(r'"([^"]+)"', m.group(2))
            for title in titles:
                tracks.append({"rank": rank, "title": title, "artist": artist, "plays": plays})
                rank += 1
            continue
        m = TRACK_LINE_RE.match(line)
        if m:
            tracks.append({
                "rank": rank, "title": m.group(1).strip(),
                "artist": m.group(2).strip(), "plays": int(m.group(3)),
            })
            rank += 1
    return tracks


def _ingest_one_review(conn, path: str, review_date: str, generator: str) -> int:
    mtime, size = _file_stat(path)
    if _already_ingested(conn, path, mtime, size):
        return 0

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    sections = _split_sections(raw)
    artists_body = ""
    tracks_body = ""
    for header, body in sections.items():
        if header.startswith("top artists"):
            artists_body = body
        elif header.startswith("top tracks"):
            tracks_body = body

    is_placeholder = (
        artists_body.strip().lower() in PLACEHOLDER_BODIES
        and tracks_body.strip().lower() in PLACEHOLDER_BODIES
    )

    top_artists = [] if is_placeholder else _parse_top_artists(artists_body)
    top_tracks = [] if is_placeholder else _parse_top_tracks(tracks_body)
    is_empty_template = 1 if is_placeholder or (not top_artists and not top_tracks) else 0

    conn.execute(
        """
        INSERT INTO reviews (
            review_date, source_generator, file_path, top_artists_json,
            top_tracks_json, is_empty_template, file_mtime, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            review_date=excluded.review_date, source_generator=excluded.source_generator,
            top_artists_json=excluded.top_artists_json, top_tracks_json=excluded.top_tracks_json,
            is_empty_template=excluded.is_empty_template, file_mtime=excluded.file_mtime,
            ingested_at=excluded.ingested_at
        """,
        (
            review_date, generator.lower(), path, json.dumps(top_artists),
            json.dumps(top_tracks), is_empty_template, mtime, _now_iso(),
        ),
    )

    status = "empty_template" if is_empty_template else "ok"
    _log_ingest(conn, path, mtime, size, 1, status)
    return 1


def _ingest_reviews(conn, root: str, stats: dict) -> None:
    folder = os.path.join(root, "Claude Reviews")
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        m = REVIEW_FILENAME_RE.match(name)
        if not m:
            continue
        path = os.path.join(folder, name)
        stats["reviews"] += _ingest_one_review(conn, path, m.group(1), m.group(2))


def _ingest_one_scrobble_csv(conn, path: str) -> int:
    mtime, size = _file_stat(path)
    if _already_ingested(conn, path, mtime, size):
        return 0

    affected = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            played_at_raw = row.get("PlayedAt", "").strip()
            if not played_at_raw:
                continue
            try:
                played_at = datetime.strptime(played_at_raw, "%m/%d/%Y %I:%M:%S %p").isoformat()
            except ValueError:
                continue
            artist = (row.get("Artist") or "").strip()
            track = (row.get("Track") or "").strip()
            if not artist or not track:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO scrobbles (artist, track, album, played_at, url, source_file) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (artist, track, (row.get("Album") or "").strip(), played_at, (row.get("Url") or "").strip(), path),
            )
            affected += cur.rowcount

    _log_ingest(conn, path, mtime, size, affected, "ok")
    return affected


def sync_live_scrobbles(conn, lastfm) -> int:
    """Pull recent plays directly from the Last.fm API into the same `scrobbles` ledger CSV
    import writes to, so Top Artists et al. don't lag behind a manual CSV export/refresh."""
    row = conn.execute("SELECT MAX(played_at) FROM scrobbles").fetchone()
    last_played_at = row[0] if row else None
    if last_played_at:
        from_ts = int(datetime.fromisoformat(last_played_at).timestamp())
    else:
        from_ts = int((datetime.now() - timedelta(days=7)).timestamp())

    tracks = lastfm.get_recent_tracks(from_ts=from_ts)

    affected = 0
    for t in tracks:
        if t.get("now_playing") or not t.get("uts"):
            continue
        artist = (t.get("artist") or "").strip()
        track = (t.get("track") or "").strip()
        if not artist or not track:
            continue
        played_at = datetime.fromtimestamp(t["uts"]).isoformat()
        cur = conn.execute(
            "INSERT OR IGNORE INTO scrobbles (artist, track, album, played_at, url, source_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artist, track, (t.get("album") or "").strip(), played_at, "", "lastfm_api_live"),
        )
        affected += cur.rowcount

    conn.commit()
    return affected


def _ingest_scrobbles(conn, root: str, stats: dict) -> None:
    folder = os.path.join(root, "Last.fm Exports")
    if not os.path.isdir(folder):
        return
    for name in os.listdir(folder):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(folder, name)
        stats["scrobbles"] += _ingest_one_scrobble_csv(conn, path)


def normalize_playlist_name(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def _upsert_playlist_row(conn, row: dict) -> int:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO playlist_tracks (
            playlist_name, track_uri, track_name, artist_names, album_name,
            album_release_date, duration_ms, popularity, isrc, added_at,
            export_date, source_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["playlist_name"], row.get("track_uri"), row["track_name"], row["artist_names"],
            row.get("album_name"), row.get("album_release_date"), row.get("duration_ms"),
            row.get("popularity"), row.get("isrc"), row.get("added_at"),
            row["export_date"], row["source_file"],
        ),
    )
    return cur.rowcount


def _csv_row_to_playlist_row(csv_row: dict, playlist_name: str, export_date: str, source_file: str) -> dict:
    track_name = (csv_row.get(PLAYLIST_CSV_FIELDS["track_name"]) or "").strip()
    artist_names = (csv_row.get(PLAYLIST_CSV_FIELDS["artist_names"]) or "").strip()
    duration_raw = (csv_row.get(PLAYLIST_CSV_FIELDS["duration_ms"]) or "").strip()
    popularity_raw = (csv_row.get(PLAYLIST_CSV_FIELDS["popularity"]) or "").strip()
    return {
        "playlist_name": playlist_name,
        "track_uri": (csv_row.get(PLAYLIST_CSV_FIELDS["track_uri"]) or "").strip() or None,
        "track_name": track_name,
        "artist_names": artist_names,
        "album_name": (csv_row.get(PLAYLIST_CSV_FIELDS["album_name"]) or "").strip() or None,
        "album_release_date": (csv_row.get(PLAYLIST_CSV_FIELDS["album_release_date"]) or "").strip() or None,
        "duration_ms": int(duration_raw) if duration_raw.isdigit() else None,
        "popularity": int(popularity_raw) if popularity_raw.isdigit() else None,
        "isrc": (csv_row.get(PLAYLIST_CSV_FIELDS["isrc"]) or "").strip() or None,
        "added_at": (csv_row.get(PLAYLIST_CSV_FIELDS["added_at"]) or "").strip() or None,
        "export_date": export_date,
        "source_file": source_file,
    }


def _ingest_one_playlist_csv(conn, path: str, playlist_name: str, export_date: str) -> int:
    mtime, size = _file_stat(path)
    if _already_ingested(conn, path, mtime, size):
        return 0

    affected = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for csv_row in reader:
            row = _csv_row_to_playlist_row(csv_row, playlist_name, export_date, path)
            if not row["track_name"]:
                continue
            affected += _upsert_playlist_row(conn, row)

    _log_ingest(conn, path, mtime, size, affected, "ok")
    return affected


def _ingest_loose_playlists(conn, folder: str, stats: dict) -> None:
    for name in os.listdir(folder):
        m = LOOSE_PLAYLIST_RE.match(name)
        if not m:
            continue
        export_date, raw_name = m.group(1), m.group(2)
        path = os.path.join(folder, name)
        stats["playlist_tracks"] += _ingest_one_playlist_csv(
            conn, path, normalize_playlist_name(raw_name), export_date
        )


def _ingest_one_archive_zip(conn, zip_path: str, export_date: str) -> int:
    mtime, size = _file_stat(zip_path)
    if _already_ingested(conn, zip_path, mtime, size):
        return 0

    affected = 0
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            if not member.endswith(".csv"):
                continue
            playlist_name = normalize_playlist_name(os.path.splitext(os.path.basename(member))[0])
            source_file = f"{zip_path}!{member}"
            with z.open(member) as raw_f:
                text = raw_f.read().decode("utf-8-sig")
            reader = csv.DictReader(text.splitlines())
            for csv_row in reader:
                row = _csv_row_to_playlist_row(csv_row, playlist_name, export_date, source_file)
                if not row["track_name"]:
                    continue
                affected += _upsert_playlist_row(conn, row)

    _log_ingest(conn, zip_path, mtime, size, affected, "ok")
    return affected


def _ingest_archive_zips(conn, archive_folder: str, stats: dict) -> None:
    if not os.path.isdir(archive_folder):
        return
    for name in os.listdir(archive_folder):
        m = ARCHIVE_ZIP_RE.match(name)
        if not m:
            continue
        path = os.path.join(archive_folder, name)
        stats["playlist_tracks"] += _ingest_one_archive_zip(conn, path, m.group(1))


def _ingest_playlists(conn, root: str, stats: dict) -> None:
    folder = os.path.join(root, "Spotify Playlists")
    if not os.path.isdir(folder):
        return
    _ingest_loose_playlists(conn, folder, stats)
    _ingest_archive_zips(conn, os.path.join(folder, "Archive Zips"), stats)


def run_ingest(root: str = MUSIC_DISCOVERY_ROOT) -> dict:
    stats = {"taste_profiles": 0, "reviews": 0, "scrobbles": 0, "playlist_tracks": 0}
    conn = db.get_connection()
    try:
        _ingest_taste_profiles(conn, root, stats)
        _ingest_reviews(conn, root, stats)
        _ingest_scrobbles(conn, root, stats)
        _ingest_playlists(conn, root, stats)
        conn.commit()
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    db.init_db()
    print(run_ingest())
