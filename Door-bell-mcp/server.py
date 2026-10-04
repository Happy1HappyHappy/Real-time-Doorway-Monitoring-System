"""
Authors: Claire Liu, Yu-Jing Wei
Description: MCP server that lets an AI assistant (Claude Code, Claude Desktop, Cursor, ...) work
with the Door-bell system: see who is at the door right now, look through past visits and VLM
threat verdicts, grab a live camera frame, and give people nicknames. It only talks to the Java
backend's REST API and to MediaMTX (through ffmpeg); it never touches the databases directly.

Env vars:
  DOORBELL_API_URL        - Java backend, or nginx in front of it (default: "http://localhost:8080")
  DOORBELL_API_USER       - basic-auth user when DOORBELL_API_URL is the nginx front door (optional)
  DOORBELL_API_PASSWORD   - basic-auth password (optional)
  DOORBELL_RTSP_URL       - MediaMTX RTSP base URL (default: "rtsp://localhost:8554")
  DOORBELL_CAMERAS        - comma-separated camera IDs (default: "cam-01,cam-02")
  DOORBELL_DATA_TZ        - timezone the workers stamp events in (default: "UTC", true in Docker)
  DOORBELL_SNAPSHOT_WIDTH - max width of snapshots in pixels (default: 768)
"""

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

# ── Config ───────────────────────────────────────────────────────────────────
API_URL = os.environ.get("DOORBELL_API_URL", "http://localhost:8080").rstrip("/")
API_USER = os.environ.get("DOORBELL_API_USER", "")
API_PASSWORD = os.environ.get("DOORBELL_API_PASSWORD", "")
RTSP_URL = os.environ.get("DOORBELL_RTSP_URL", "rtsp://localhost:8554").rstrip("/")
CAMERAS = [c.strip() for c in os.environ.get("DOORBELL_CAMERAS", "cam-01,cam-02").split(",") if c.strip()]
DEFAULT_CAMERA = CAMERAS[0] if CAMERAS else "cam-01"
DATA_TZ = ZoneInfo(os.environ.get("DOORBELL_DATA_TZ", "UTC"))
SNAPSHOT_WIDTH = int(os.environ.get("DOORBELL_SNAPSHOT_WIDTH", "768"))

HTTP_TIMEOUT_SEC = 10
SNAPSHOT_TIMEOUT_SEC = 15
THREAT_LEVELS = ("safe", "watch", "alert")
# Repeated verdicts for one track more than this far apart count as separate episodes.
EPISODE_GAP = timedelta(minutes=10)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer(
    name="door-bell",
    instructions=(
        "Tools for the Door-bell doorway camera system. People are recognised by appearance, so a "
        "person_id stays the same across visits and cameras; names are nicknames the user set. "
        "VLM verdicts are safe, watch, or alert. Times in results are the user's local time. "
        "Use get_snapshot when you want to see the doorway yourself."
    ),
)


# ── Time handling ────────────────────────────────────────────────────────────
# The workers write naive timestamps in DATA_TZ. Tools take and return local time, which is what
# the person asking means by "this morning", so everything is converted at the edges.

_RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)\s*([mhdw])$")
_UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}


def _now() -> datetime:
    return datetime.now().astimezone()


def parse_time(text: str, now: datetime) -> datetime:
    """Read "30m", "2h", "1d", "1w", "now", "today", "yesterday", or an ISO date/time.

    Relative values count back from now; "today" and "yesterday" mean local midnight; ISO values
    without an offset are local time.
    """
    value = text.strip().lower()
    if value == "now":
        return now
    if value in ("today", "yesterday"):
        day = now.date() - timedelta(days=1 if value == "yesterday" else 0)
        return datetime.combine(day, time.min).astimezone()
    match = _RELATIVE.match(value)
    if match:
        return now - float(match.group(1)) * _UNITS[match.group(2)]
    try:
        return datetime.fromisoformat(text.strip()).astimezone()
    except ValueError:
        raise ToolError(
            f"Can't read the time {text!r}. Use '30m', '2h', '1d', 'today', 'yesterday', "
            "or a local time like '2026-10-03T09:00'."
        ) from None


def window(since: str, until: str | None) -> tuple[datetime, datetime]:
    now = _now()
    start = parse_time(since, now)
    end = parse_time(until, now) if until else now
    if start > end:
        raise ToolError(f"since ({start:%Y-%m-%d %H:%M}) is after until ({end:%Y-%m-%d %H:%M}).")
    return start, end


def to_api_time(moment: datetime) -> str:
    return moment.astimezone(DATA_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def from_api(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=DATA_TZ).astimezone()


def local_iso(text: str | None) -> str | None:
    return from_api(text).isoformat(timespec="seconds") if text else None


# ── Backend calls ────────────────────────────────────────────────────────────

def call_api(method: str, path: str, params: dict | None = None, body: dict | None = None,
             not_found: str | None = None):
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    request = urllib.request.Request(f"{API_URL}{path}" + (f"?{query}" if query else ""), method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    if API_USER:
        token = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ToolError("The API asked for a login. Set DOORBELL_API_USER and DOORBELL_API_PASSWORD.") from None
        if e.code == 404 and not_found:
            raise ToolError(not_found) from None
        if e.code == 404:
            raise ToolError(
                f"{path} isn't on this backend. It's probably an older build; rebuild it with "
                "`docker compose up --build -d java-backend`."
            ) from None
        detail = e.read().decode(errors="replace")[:300]
        raise ToolError(f"Backend returned HTTP {e.code} for {method} {path}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ToolError(
            f"Can't reach the Door-bell backend at {API_URL} ({getattr(e, 'reason', e)}). "
            "Is the stack running (`docker compose up -d`)?"
        ) from None
    return json.loads(raw) if raw else None


def grab_frame(url: str) -> bytes:
    """Decode one frame from url and return it as a JPEG no wider than SNAPSHOT_WIDTH."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ToolError("ffmpeg isn't installed here, so frames can't be grabbed (`brew install ffmpeg`).")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += ["-i", url, "-frames:v", "1", "-vf", f"scale='min({SNAPSHOT_WIDTH},iw)':-2",
            "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=SNAPSHOT_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        raise ToolError(f"Timed out after {SNAPSHOT_TIMEOUT_SEC}s waiting for a frame from {url}.") from None
    if result.returncode != 0 or not result.stdout:
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        raise ToolError(
            f"Couldn't grab a frame from {url} ({lines[-1] if lines else 'no output'}). "
            "Is a camera publishing to that path?"
        )
    return result.stdout


def group_verdicts(items: list[dict]) -> list[dict]:
    """Collapse repeated verdicts into episodes, newest first.

    vlm-worker re-checks a person every few seconds, so a two-minute visit is ~25 rows. An
    episode is one camera + track + level with no gap longer than EPISODE_GAP; the gap matters
    because track IDs start over when a worker restarts.
    """
    episodes, current = [], {}
    for v in sorted(items, key=lambda v: from_api(v["analyzedAt"])):
        at = from_api(v["analyzedAt"])
        key = (v["cameraId"], v["trackId"], v["threatLevel"])
        ep = current.get(key)
        if ep is None or at - ep["last"] > EPISODE_GAP:
            ep = {"first": at, "last": at, "count": 0, "person_id": None, "name": None,
                  "descriptions": [], "reasons": [], "row": v}
            episodes.append(ep)
            current[key] = ep
        ep["last"] = at
        ep["count"] += 1
        # The first verdicts can arrive before ReID has matched the track, so keep the latest match.
        if v.get("personId") is not None:
            ep["person_id"], ep["name"] = v["personId"], v.get("nickname")
        for field, seen in (("description", ep["descriptions"]), ("reason", ep["reasons"])):
            text = (v.get(field) or "").strip()
            if text and text not in seen:
                seen.append(text)

    episodes.sort(key=lambda ep: ep["last"], reverse=True)
    return [{
        "camera": ep["row"]["cameraId"],
        "track": ep["row"]["trackId"],
        "person_id": ep["person_id"],
        "name": ep["name"],
        "level": ep["row"]["threatLevel"],
        "times_given": ep["count"],
        "first": ep["first"].isoformat(timespec="seconds"),
        "last": ep["last"].isoformat(timespec="seconds"),
        "descriptions": ep["descriptions"][-3:][::-1],
        "reasons": ep["reasons"][-3:][::-1],
    } for ep in episodes]


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_live_status() -> dict:
    """Who is at the door right now.

    in_frame lists every identified person currently on a camera, with the latest VLM verdict
    for them (null until the VLM answers). unidentified_recent holds VLM verdicts from the last
    30 seconds about people re-identification hasn't matched, e.g. someone who stayed too far
    from the camera. Both empty means nobody is there.
    """
    now = _now()
    live = call_api("GET", "/api/live") or []
    recent = call_api("GET", "/api/analyses", {
        "since": to_api_time(now - timedelta(seconds=30)),
        "until": to_api_time(now + timedelta(seconds=5)),
        "limit": 200,
    })

    in_frame = {(t["cameraId"], t["trackId"]) for t in live}
    unidentified = {}
    for v in recent["items"]:  # newest first, so the first row per track is its latest verdict
        key = (v["cameraId"], v["trackId"])
        if v.get("personId") is not None or key in in_frame or key in unidentified:
            continue
        unidentified[key] = {
            "camera": v["cameraId"],
            "track": v["trackId"],
            "level": v["threatLevel"],
            "description": v["description"],
            "reason": v["reason"],
            "verdict_at": local_iso(v["analyzedAt"]),
        }

    return {
        "now": now.isoformat(timespec="seconds"),
        "in_frame": [{
            "camera": t["cameraId"],
            "person_id": t["personId"],
            "name": t["nickname"],
            "in_frame_since": local_iso(t["firstSeenAt"]),
            "level": t["threatLevel"],
            "description": t["description"],
            "reason": t["reason"],
            "verdict_at": local_iso(t["analyzedAt"]),
        } for t in live],
        "unidentified_recent": list(unidentified.values()),
    }


@mcp.tool(annotations=READ_ONLY, description=(
    "Grab the current frame from a camera so you can look at the doorway yourself.\n\n"
    f"camera_id is one of: {', '.join(CAMERAS)}. The annotated stream has the YOLO person boxes "
    "drawn on it; pass annotated=false for the raw camera image. Takes a second or two, and fails "
    "if no camera is publishing to that path."
))
def get_snapshot(camera_id: str = DEFAULT_CAMERA, annotated: bool = True) -> Image:
    if camera_id not in CAMERAS:
        raise ToolError(f"Unknown camera {camera_id!r}. Cameras: {', '.join(CAMERAS)}.")
    path = f"{camera_id}/annotated" if annotated else camera_id
    return Image(data=grab_frame(f"{RTSP_URL}/{path}"), format="jpeg")


@mcp.tool(annotations=READ_ONLY)
def list_people(seen_since: str | None = None, limit: int = 100) -> dict:
    """Everyone the system has identified, most recently seen first.

    A person_id stays the same across visits and cameras. last_seen is when their most recent
    visit started. seen_since ("today", "2h", "2026-10-01") keeps only people seen since then.
    """
    cutoff = parse_time(seen_since, _now()) if seen_since else None
    people = []
    for p in call_api("GET", "/api/persons") or []:
        last_seen = from_api(p["lastSeenAt"]) if p.get("lastSeenAt") else None
        if cutoff and (last_seen is None or last_seen < cutoff):
            continue
        people.append((last_seen, p))
    people.sort(key=lambda pair: pair[0].timestamp() if pair[0] else float("-inf"), reverse=True)

    return {
        "total": len(people),
        "people": [{
            "person_id": p["id"],
            "name": p.get("nickname"),
            "first_seen": local_iso(p.get("firstSeenAt")),
            "first_camera": p.get("firstSeenCamera"),
            "last_seen": last_seen.isoformat(timespec="seconds") if last_seen else None,
        } for last_seen, p in people[:limit]],
    }


@mcp.tool(annotations=READ_ONLY)
def get_visits(since: str = "24h", until: str | None = None, camera_id: str | None = None,
               person_id: int | None = None, limit: int = 50) -> dict:
    """When people showed up: one row per time a person appeared on a camera, newest first.

    since/until take "30m", "2h", "1d", "today", "yesterday", or a local time like
    "2026-10-03T09:00"; until defaults to now. For all of yesterday, pass since="yesterday" and
    until="today". Someone who stays in view is one row, and walking out of frame and back in
    usually makes a new one. total counts every visit in the window, even past limit.
    """
    start, end = window(since, until)
    data = call_api("GET", "/api/detections", {
        "since": to_api_time(start), "until": to_api_time(end),
        "cameraId": camera_id, "personId": person_id, "limit": limit,
    })
    return {
        "window": {"since": start.isoformat(timespec="seconds"), "until": end.isoformat(timespec="seconds")},
        "total": data["total"],
        "visits": [{
            "time": local_iso(d["detectedAt"]),
            "camera": d["cameraId"],
            "person_id": d["personId"],
            "name": d["nickname"],
        } for d in data["items"]],
    }


@mcp.tool(annotations=READ_ONLY)
def get_threat_events(since: str = "24h", until: str | None = None,
                      min_level: Literal["safe", "watch", "alert"] = "watch",
                      camera_id: str | None = None, person_id: int | None = None,
                      limit: int = 30) -> dict:
    """What the VLM flagged, newest first.

    vlm-worker checks each person when they appear and again every few seconds while they stay,
    so repeated verdicts are grouped into episodes: one per camera, track and level, with how
    many times it was given, first/last time, and up to three distinct descriptions and reasons.
    min_level "watch" (default) returns watch and alert, "alert" only alerts, "safe" everything.
    since/until work like get_visits.
    """
    start, end = window(since, until)
    data = call_api("GET", "/api/analyses", {
        "since": to_api_time(start), "until": to_api_time(end), "minLevel": min_level,
        "cameraId": camera_id, "personId": person_id, "limit": 1000,
    })
    episodes = group_verdicts(data["items"])
    return {
        "window": {"since": start.isoformat(timespec="seconds"), "until": end.isoformat(timespec="seconds")},
        "verdicts_in_window": data["total"],
        "episodes": episodes[:limit],
        "truncated": data["total"] > len(data["items"]) or len(episodes) > limit,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=False))
def set_nickname(person_id: int, nickname: str) -> dict:
    """Name a person (e.g. "mail carrier", "Mom") so the dashboard and these tools show the name
    instead of "Person #<id>". An empty nickname clears it."""
    person = call_api("PUT", f"/api/persons/{person_id}/nickname", body={"nickname": nickname},
                      not_found=f"There is no person with ID {person_id}.")
    return {"person_id": person["id"], "name": person.get("nickname")}


# ── Prompts ──────────────────────────────────────────────────────────────────

@mcp.prompt()
def door_report(since: str = "today") -> str:
    """Summarise door activity: who came by, when, and anything the VLM flagged."""
    return (
        f"Write a short report of activity at the door since {since}, using the door-bell tools:\n"
        f"1. get_visits(since={since!r}, limit=200) for who showed up and when.\n"
        f"2. get_threat_events(since={since!r}) for anything flagged watch or alert.\n"
        "3. get_live_status() for who is there right now.\n"
        "Call people by their name if they have one, otherwise Person #<id>. Group visits by person "
        "with arrival times, list every watch/alert episode with its reason, and finish with who is at "
        "the door now. Keep it short and don't go beyond what the tools return."
    )


if __name__ == "__main__":
    mcp.run()
