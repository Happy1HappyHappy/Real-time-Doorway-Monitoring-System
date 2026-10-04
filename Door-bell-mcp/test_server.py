"""
Authors: Claire Liu, Yu-Jing Wei
Description: Tests for the Door-bell MCP server — time parsing and UTC/local conversion, verdict
grouping, each tool against a stubbed backend, HTTP error handling against a real local server,
a real ffmpeg snapshot, and the MCP protocol surface through the SDK's in-memory client.

Run: pytest -v   (from Door-bell-mcp/, with the venv active)
"""
import asyncio
import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

import server

UTC = ZoneInfo("UTC")


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ── Time handling ────────────────────────────────────────────────────────────

class TestParseTime:
    NOW = datetime(2026, 10, 3, 15, 30, tzinfo=timezone(timedelta(hours=-7)))

    @pytest.mark.parametrize("text, delta", [
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("1.5d", timedelta(days=1.5)),
        ("1w", timedelta(weeks=1)),
        (" 2H ", timedelta(hours=2)),
    ])
    def test_relative(self, text, delta):
        assert server.parse_time(text, self.NOW) == self.NOW - delta

    def test_now(self):
        assert server.parse_time("now", self.NOW) == self.NOW

    def test_today_and_yesterday_are_local_midnight(self):
        today = server.parse_time("today", self.NOW)
        yesterday = server.parse_time("yesterday", self.NOW)
        assert (today.hour, today.minute, today.date()) == (0, 0, self.NOW.date())
        assert yesterday.date() == self.NOW.date() - timedelta(days=1)
        assert today.utcoffset() == datetime(2026, 10, 3).astimezone().utcoffset()

    def test_naive_iso_is_local_time(self):
        assert server.parse_time("2026-10-03T09:00", self.NOW) == datetime(2026, 10, 3, 9, 0).astimezone()

    def test_iso_with_offset_keeps_the_instant(self):
        assert server.parse_time("2026-10-03T16:00:00Z", self.NOW) == utc("2026-10-03T16:00:00")

    @pytest.mark.parametrize("text", ["last tuesday", "2 hours", "-3h", ""])
    def test_rejects_unreadable_times(self, text):
        with pytest.raises(ToolError, match="Can't read the time"):
            server.parse_time(text, self.NOW)


def test_window_rejects_since_after_until():
    with pytest.raises(ToolError, match="is after until"):
        server.window("1h", "2h")


def test_api_times_round_trip_through_utc(monkeypatch):
    monkeypatch.setattr(server, "DATA_TZ", UTC)
    local = datetime(2026, 10, 3, 9, 15, 2).astimezone()

    api_text = server.to_api_time(local)

    assert api_text == local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    assert server.from_api(api_text) == local
    assert server.local_iso(api_text) == local.isoformat(timespec="seconds")
    assert server.local_iso(None) is None


# ── Verdict grouping ─────────────────────────────────────────────────────────

def verdict(at, level="watch", camera="cam-01", track=7, person=None, nickname=None,
            description="wearing a black mask", reason="face covering"):
    return {"id": 0, "cameraId": camera, "trackId": track, "personId": person, "nickname": nickname,
            "threatLevel": level, "description": description, "reason": reason, "analyzedAt": at}


def test_group_verdicts_collapses_repeats_and_keeps_latest_person():
    items = [  # newest first, like the API
        verdict("2026-10-03T16:00:10", person=42, nickname="Courier", description="peering in the window"),
        verdict("2026-10-03T16:00:05", person=42, nickname="Courier"),
        verdict("2026-10-03T16:00:00"),  # came back before ReID matched the track
    ]

    [episode] = server.group_verdicts(items)

    assert episode["times_given"] == 3
    assert (episode["person_id"], episode["name"]) == (42, "Courier")
    assert episode["descriptions"] == ["peering in the window", "wearing a black mask"]
    assert episode["reasons"] == ["face covering"]
    assert episode["first"] == server.local_iso("2026-10-03T16:00:00")
    assert episode["last"] == server.local_iso("2026-10-03T16:00:10")


def test_group_verdicts_splits_on_level_track_and_long_gaps():
    items = [
        verdict("2026-10-03T18:00:00"),                  # same track id 2 h later: worker restarted
        verdict("2026-10-03T16:00:20", level="alert"),
        verdict("2026-10-03T16:00:10", track=8),
        verdict("2026-10-03T16:00:00"),
    ]

    episodes = server.group_verdicts(items)

    assert [(e["track"], e["level"], e["last"]) for e in episodes] == [
        (7, "watch", server.local_iso("2026-10-03T18:00:00")),
        (7, "alert", server.local_iso("2026-10-03T16:00:20")),
        (8, "watch", server.local_iso("2026-10-03T16:00:10")),
        (7, "watch", server.local_iso("2026-10-03T16:00:00")),
    ]


# ── Tools against a stubbed backend ──────────────────────────────────────────

class FakeApi:
    """Stands in for call_api: returns canned JSON per path and records every call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, path, params=None, body=None, not_found=None):
        self.calls.append((method, path, params, body))
        return self.responses[path]


def test_get_live_status_merges_live_tracks_and_unidentified_verdicts(monkeypatch):
    monkeypatch.setattr(server, "DATA_TZ", UTC)
    api = FakeApi({
        "/api/live": [{
            "cameraId": "cam-01", "trackId": 3, "personId": 42, "nickname": "Mail carrier",
            "firstSeenAt": "2026-10-03T16:14:00", "lastSeenAt": "2026-10-03T16:15:00",
            "threatLevel": "safe", "description": "holding a parcel", "reason": "ordinary item",
            "analyzedAt": "2026-10-03T16:14:58",
        }],
        "/api/analyses": {"total": 4, "items": [
            verdict("2026-10-03T16:14:59", camera="cam-02", track=9, level="alert", description="holding a bat"),
            verdict("2026-10-03T16:14:58", camera="cam-01", track=3, person=42),  # already in frame
            verdict("2026-10-03T16:14:55", camera="cam-02", track=9, level="watch"),  # older, same track
            verdict("2026-10-03T16:14:50", camera="cam-02", track=5, person=17),  # matched but gone
        ]},
    })
    monkeypatch.setattr(server, "call_api", api)

    status = server.get_live_status()

    assert status["in_frame"] == [{
        "camera": "cam-01", "person_id": 42, "name": "Mail carrier",
        "in_frame_since": server.local_iso("2026-10-03T16:14:00"),
        "level": "safe", "description": "holding a parcel", "reason": "ordinary item",
        "verdict_at": server.local_iso("2026-10-03T16:14:58"),
    }]
    assert [(u["camera"], u["track"], u["level"]) for u in status["unidentified_recent"]] == [("cam-02", 9, "alert")]
    since = api.calls[1][2]["since"]
    assert utc(api.calls[1][2]["until"]) - utc(since) == timedelta(seconds=35)


def test_get_visits_sends_utc_window_and_filters(monkeypatch):
    monkeypatch.setattr(server, "DATA_TZ", UTC)
    api = FakeApi({"/api/detections": {"total": 7, "items": [{
        "id": 5, "personId": 42, "nickname": None, "cameraId": "cam-01", "trackId": 3,
        "confidence": 0.9, "detectedAt": "2026-10-03T16:14:00.123456",
    }]}})
    monkeypatch.setattr(server, "call_api", api)

    result = server.get_visits(since="2026-10-03T08:00", until="2026-10-03T10:00", camera_id="cam-01",
                               person_id=42, limit=1)

    _, path, params, _ = api.calls[0]
    assert path == "/api/detections"
    assert params == {
        "since": server.to_api_time(datetime(2026, 10, 3, 8, 0).astimezone()),
        "until": server.to_api_time(datetime(2026, 10, 3, 10, 0).astimezone()),
        "cameraId": "cam-01", "personId": 42, "limit": 1,
    }
    assert result["total"] == 7
    assert result["visits"] == [{"time": server.local_iso("2026-10-03T16:14:00.123456"),
                                 "camera": "cam-01", "person_id": 42, "name": None}]


def test_get_threat_events_groups_and_flags_truncation(monkeypatch):
    monkeypatch.setattr(server, "DATA_TZ", UTC)
    api = FakeApi({"/api/analyses": {"total": 1500, "items": [
        verdict("2026-10-03T16:00:05", track=1), verdict("2026-10-03T16:00:00", track=1),
        verdict("2026-10-03T15:00:00", track=2, level="alert"),
    ]}})
    monkeypatch.setattr(server, "call_api", api)

    result = server.get_threat_events(since="1d", min_level="alert", limit=1)

    assert api.calls[0][2]["minLevel"] == "alert"
    assert result["verdicts_in_window"] == 1500
    assert len(result["episodes"]) == 1 and result["episodes"][0]["track"] == 1
    assert result["truncated"] is True


def test_list_people_filters_by_last_seen_and_sorts(monkeypatch):
    monkeypatch.setattr(server, "DATA_TZ", UTC)
    now = datetime.now(UTC)
    fmt = lambda dt: dt.replace(tzinfo=None).isoformat()  # noqa: E731
    api = FakeApi({"/api/persons": [
        {"id": 1, "nickname": "Mom", "firstSeenCamera": "cam-01",
         "firstSeenAt": fmt(now - timedelta(days=9)), "lastSeenAt": fmt(now - timedelta(days=3))},
        {"id": 2, "nickname": None, "firstSeenCamera": "cam-02",
         "firstSeenAt": fmt(now - timedelta(hours=2)), "lastSeenAt": fmt(now - timedelta(minutes=5))},
        {"id": 3, "nickname": None, "firstSeenCamera": "cam-01",
         "firstSeenAt": fmt(now - timedelta(hours=1)), "lastSeenAt": fmt(now - timedelta(minutes=50))},
    ]})
    monkeypatch.setattr(server, "call_api", api)

    assert [p["person_id"] for p in server.list_people()["people"]] == [2, 3, 1]
    recent = server.list_people(seen_since="1d")
    assert recent["total"] == 2
    assert [p["person_id"] for p in server.list_people(limit=1)["people"]] == [2]


def test_set_nickname_puts_the_new_name(monkeypatch):
    api = FakeApi({"/api/persons/42/nickname": {"id": 42, "nickname": "Courier"}})
    monkeypatch.setattr(server, "call_api", api)

    assert server.set_nickname(42, "Courier") == {"person_id": 42, "name": "Courier"}
    assert api.calls[0][0] == "PUT" and api.calls[0][3] == {"nickname": "Courier"}


# ── HTTP error handling against a real server ────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/live"):
            self._send(200, json.dumps([{"auth": self.headers.get("Authorization")}]))
        elif self.path.startswith("/api/secret"):
            self._send(401, "")
        elif self.path.startswith("/api/boom"):
            self._send(500, '{"error": "kaboom"}')
        else:
            self._send(404, "")

    do_PUT = do_GET  # noqa: N815

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_api(monkeypatch):
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "API_URL", f"http://127.0.0.1:{httpd.server_port}")
    yield httpd
    httpd.shutdown()


def test_call_api_sends_basic_auth_when_configured(http_api, monkeypatch):
    monkeypatch.setattr(server, "API_USER", "demo")
    monkeypatch.setattr(server, "API_PASSWORD", "pw")
    assert server.call_api("GET", "/api/live") == [{"auth": "Basic ZGVtbzpwdw=="}]


def test_call_api_turns_http_errors_into_readable_tool_errors(http_api):
    with pytest.raises(ToolError, match="asked for a login"):
        server.call_api("GET", "/api/secret")
    with pytest.raises(ToolError, match="rebuild it"):
        server.call_api("GET", "/api/analyses")
    with pytest.raises(ToolError, match="There is no person with ID 9"):
        server.call_api("PUT", "/api/persons/9/nickname", body={}, not_found="There is no person with ID 9.")
    with pytest.raises(ToolError, match="HTTP 500.*kaboom"):
        server.call_api("GET", "/api/boom")


def test_call_api_reports_unreachable_backend(monkeypatch):
    monkeypatch.setattr(server, "API_URL", "http://127.0.0.1:9")  # discard port, nothing listens
    with pytest.raises(ToolError, match="Can't reach the Door-bell backend"):
        server.call_api("GET", "/api/live")


# ── Snapshots ────────────────────────────────────────────────────────────────

def test_get_snapshot_rejects_unknown_camera():
    with pytest.raises(ToolError, match="Unknown camera"):
        server.get_snapshot("cam-99")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_get_snapshot_returns_a_scaled_jpeg(tmp_path, monkeypatch):
    # MediaMTX serves cam-01/annotated; a file at the same relative path stands in for it.
    clip = tmp_path / "cam-01" / "annotated"
    clip.parent.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=5",
                    "-frames:v", "5", "-c:v", "mpeg4", "-f", "mp4", str(clip)], check=True)
    monkeypatch.setattr(server, "RTSP_URL", str(tmp_path))

    image = server.get_snapshot("cam-01")

    assert image.to_image_content().mime_type == "image/jpeg"
    jpeg = image.data
    assert jpeg[:2] == b"\xff\xd8"
    width = int(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", "-"],
                               input=jpeg, capture_output=True, check=True).stdout)
    assert width == server.SNAPSHOT_WIDTH


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_get_snapshot_explains_a_missing_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RTSP_URL", str(tmp_path))
    with pytest.raises(ToolError, match="Is a camera publishing"):
        server.get_snapshot("cam-02")


# ── MCP protocol surface ─────────────────────────────────────────────────────

def test_protocol_lists_tools_with_annotations_and_serves_the_prompt():
    async def run():
        async with Client(server.mcp) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            prompt = await client.get_prompt("door_report", {"since": "2h"})
            bad = await client.call_tool("get_threat_events", {"min_level": "panic"})
            return tools, prompt, bad

    tools, prompt, bad = asyncio.run(run())

    assert set(tools) == {"get_live_status", "get_snapshot", "list_people", "get_visits",
                          "get_threat_events", "set_nickname"}
    assert all(tools[name].annotations.read_only_hint for name in set(tools) - {"set_nickname"})
    assert tools["set_nickname"].annotations.read_only_hint is False
    assert "cam-01, cam-02" in tools["get_snapshot"].description
    assert tools["get_threat_events"].input_schema["properties"]["min_level"]["enum"] == ["safe", "watch", "alert"]
    assert "get_visits(since='2h'" in prompt.messages[0].content.text
    assert bad.is_error
