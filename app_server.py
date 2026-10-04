#!/usr/bin/env python3
"""Local Torn Warroom web server and short-lived API bridge.

The Torn API key is accepted only in a POST body, forwarded to Torn over HTTPS,
and never written to disk or included in server request logs.
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
TORN_V2 = "https://api.torn.com/v2"
TORN_V1 = "https://api.torn.com"
STATIC_PATHS = {
    "/", "/index.html", "/manifest.webmanifest", "/service-worker.js",
    "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png",
    "/downloads/owchowch-personal-war-room-standalone.zip",
}


def error_message(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not error:
        return None
    if isinstance(error, dict):
        code = error.get("code", "")
        message = error.get("error") or error.get("message") or "API request failed"
        return f"Torn API {code}: {message}" if code else str(message)
    return str(error)


def fetch_json(url: str) -> tuple[dict | list | None, str | None]:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Torn-Warroom/1.0 (local user app)"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=18) as response:
            raw = response.read(3_000_000)
        payload = json.loads(raw.decode("utf-8"))
        return payload, error_message(payload)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read(1_000_000).decode("utf-8"))
            return payload, error_message(payload) or f"Torn returned HTTP {exc.code}"
        except Exception:
            return None, f"Torn returned HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"Could not reach Torn API: {exc}"
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"Torn returned an unreadable response: {exc}"


def get_user_faction(api_key: str) -> tuple[dict | list | None, str | None]:
    query = urlencode({"key": api_key})
    payload, error = fetch_json(f"{TORN_V2}/user/faction?{query}")
    if not error and isinstance(payload, dict):
        return payload, None
    legacy_query = urlencode({"selections": "faction", "key": api_key})
    legacy, legacy_error = fetch_json(f"{TORN_V1}/user/?{legacy_query}")
    if not legacy_error and isinstance(legacy, dict):
        return legacy, None
    return legacy or payload, legacy_error or error


def get_ranked_war_sources(api_key: str):
    """Yield progressively older official Torn faction war feeds."""
    query = urlencode({"key": api_key})
    for route in ("faction/wars", "faction/rankedwars"):
        payload, error = fetch_json(f"{TORN_V2}/{route}?{query}")
        yield payload, error, route
    legacy_query = urlencode({"selections": "rankedwars", "key": api_key})
    payload, error = fetch_json(f"{TORN_V1}/faction/?{legacy_query}")
    yield payload, error, "v1 faction rankedwars"


def get_members(faction_id: str, api_key: str) -> tuple[dict | list | None, str | None]:
    query = urlencode({"key": api_key})
    v2_url = f"{TORN_V2}/faction/{faction_id}/members?{query}"
    payload, error = fetch_json(v2_url)
    if not error and isinstance(payload, dict) and isinstance(payload.get("members"), (dict, list)):
        return payload, None

    legacy_query = urlencode({"selections": "basic", "key": api_key})
    legacy_url = f"{TORN_V1}/faction/{faction_id}?{legacy_query}"
    legacy, legacy_error = fetch_json(legacy_url)
    if legacy_error:
        return legacy or payload, legacy_error
    if not isinstance(legacy, dict) or not isinstance(legacy.get("members"), (dict, list)):
        return legacy, "Torn returned faction data without a members list"
    return legacy, None


def get_chain(api_key: str) -> tuple[dict | list | None, str | None]:
    query = urlencode({"key": api_key})
    payload, error = fetch_json(f"{TORN_V2}/faction/chain?{query}")
    if not error and isinstance(payload, dict) and isinstance(payload.get("chain"), dict):
        return payload, None

    legacy_query = urlencode({"selections": "chain", "key": api_key})
    legacy, legacy_error = fetch_json(f"{TORN_V1}/faction/?{legacy_query}")
    if legacy_error:
        return legacy or payload, legacy_error
    if not isinstance(legacy, dict) or not isinstance(legacy.get("chain"), dict):
        return legacy, "Torn returned no chain status"
    return legacy, None


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _id_and_name(value, fallback_id=""):
    if not isinstance(value, dict):
        return "", ""
    faction_id = value.get("ID", value.get("id", value.get("faction_id", value.get("factionID", fallback_id))))
    name = value.get("name", value.get("faction_name", value.get("factionName", "")))
    return (str(faction_id) if faction_id is not None else "", str(name or ""))


def extract_own_faction(payload):
    """Read the API-key owner's faction ID/name from user/faction or v1 user/faction."""
    if not isinstance(payload, dict):
        return "", ""
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("faction", "your_faction", "user_faction"):
        item = root.get(key)
        if isinstance(item, dict):
            faction_id, name = _id_and_name(item)
            if faction_id:
                return faction_id, name
    # Some responses expose the faction object directly or use flat v1 fields.
    faction_id, name = _id_and_name(root)
    if faction_id:
        return faction_id, name
    faction_id = root.get("faction_id", root.get("factionID", ""))
    name = root.get("faction_name", root.get("factionName", ""))
    return (str(faction_id) if faction_id else "", str(name or ""))


def _timestamp(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 1e12 else number
    text = str(value).strip()
    if text.isdigit():
        number = float(text)
        return number / 1000 if number > 1e12 else number
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        return 0


def _war_times(candidate):
    nested = candidate.get("war") if isinstance(candidate.get("war"), dict) else {}
    merged = {**candidate, **nested}
    start = _timestamp(merged.get("start", merged.get("start_time", merged.get("start_at", merged.get("startTime")))))
    end = _timestamp(merged.get("end", merged.get("end_time", merged.get("end_at", merged.get("endTime")))))
    winner = merged.get("winner", merged.get("winner_id", 0))
    try:
        winner = int(winner or 0)
    except (TypeError, ValueError):
        winner = 0
    status = str(merged.get("status", merged.get("state", ""))).lower()
    return start, end, winner, status


def _factions_in_war(candidate):
    nested = candidate.get("war") if isinstance(candidate.get("war"), dict) else {}
    merged = {**candidate, **nested}
    factions = merged.get("factions") or candidate.get("factions")
    found = []
    if isinstance(factions, dict):
        for key, value in factions.items():
            if isinstance(value, dict):
                fallback = key if str(key).isdigit() else ""
                faction_id, name = _id_and_name(value, fallback)
                if faction_id:
                    found.append({"id": faction_id, "name": name})
    elif isinstance(factions, list):
        for value in factions:
            if isinstance(value, dict):
                faction_id, name = _id_and_name(value)
                if faction_id:
                    found.append({"id": faction_id, "name": name})

    if len(found) < 2:
        side_pairs = (
            ("faction_a_id", "faction_a_name"), ("faction_b_id", "faction_b_name"),
            ("factionAId", "factionAName"), ("factionBId", "factionBName"),
            ("faction1_id", "faction1_name"), ("faction2_id", "faction2_name"),
        )
        direct = []
        for id_key, name_key in side_pairs:
            value = merged.get(id_key)
            if value:
                direct.append({"id": str(value), "name": str(merged.get(name_key, "") or "")})
        if len(direct) >= 2:
            found = direct[:2]

    if len(found) < 2:
        for key in ("opponent", "opponent_faction", "enemy", "enemy_faction"):
            value = merged.get(key)
            if isinstance(value, dict):
                faction_id, name = _id_and_name(value)
                if faction_id:
                    found.append({"id": faction_id, "name": name, "opponent": True})
            elif str(value or "").isdigit():
                found.append({"id": str(value), "name": "", "opponent": True})
    return found


def _candidate_objects(node, path=()):
    if isinstance(node, list):
        for item in node:
            yield from _candidate_objects(item, path)
    elif isinstance(node, dict):
        path_text = ".".join(path).lower()
        is_ranked_container = "ranked" in path_text or any("ranked" in str(k).lower() for k in node.keys())
        has_ranked_type = any("ranked" in str(node.get(k, "")).lower() for k in ("type", "war_type", "warType", "name"))
        has_war_shape = bool(node.get("factions") or node.get("opponent") or node.get("opponent_faction") or node.get("faction_a_id") or node.get("factionAId"))
        if has_war_shape and (is_ranked_container or has_ranked_type):
            yield node, path_text
        for key, value in node.items():
            yield from _candidate_objects(value, path + (str(key),))


def find_active_ranked_opponent(payload, own_faction_id):
    if not isinstance(payload, (dict, list)):
        return None
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)) else payload
    now = time.time()
    candidates = []
    for candidate, path_text in _candidate_objects(root):
        start, end, winner, status = _war_times(candidate)
        if winner or any(word in status for word in ("ended", "finished", "completed", "cancelled", "expired")):
            continue
        active_status = any(word in status for word in ("active", "started", "in progress", "ongoing", "war"))
        if start and start > now and not active_status:
            continue
        if end and end <= now and not active_status:
            continue
        factions = _factions_in_war(candidate)
        if len(factions) < 2:
            continue
        own = next((item for item in factions if own_faction_id and item["id"] == str(own_faction_id)), None)
        opponent = next((item for item in factions if item is not own and item.get("id") != str(own_faction_id)), None)
        if own is None:
            opponent = next((item for item in factions if item.get("opponent")), None)
        if not opponent or not str(opponent.get("id", "")).isdigit():
            continue
        if not active_status and not start and not end:
            # The war object lacks dates/status. Only trust this in an explicit ranked-wars feed;
            # caller checks that the candidate came from an official ranked-war selection.
            pass
        candidates.append((start, {"id": str(opponent["id"]), "name": opponent.get("name", ""), "war_id": str(candidate.get("id", candidate.get("war_id", candidate.get("warID", ""))))}))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def discover_opponent(api_key: str):
    """Resolve the opponent from the key owner's active ranked-war API data."""
    query = urlencode({"key": api_key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        own_future = pool.submit(get_user_faction, api_key)
        wars_future = pool.submit(fetch_json, f"{TORN_V2}/faction/wars?{query}")
        own_payload, own_error = own_future.result()
        wars_payload, wars_error = wars_future.result()

    own_id, own_name = extract_own_faction(own_payload)
    if not own_id and isinstance(wars_payload, dict):
        root = wars_payload.get("data") if isinstance(wars_payload.get("data"), dict) else wars_payload
        faction = root.get("faction") if isinstance(root.get("faction"), dict) else {}
        own_id, inferred_name = _id_and_name(faction)
        own_name = own_name or inferred_name
        own_id = own_id or str(root.get("faction_id", root.get("factionID", "")) or "")

    found = None
    lookup_ok = False
    lookup_errors = []
    sources_checked = []
    if wars_error:
        lookup_errors.append(wars_error)
    elif wars_payload is not None:
        lookup_ok = True
        found = find_active_ranked_opponent(wars_payload, own_id)
        sources_checked.append("faction/wars")

    if not found:
        ranked_payload, ranked_error = fetch_json(f"{TORN_V2}/faction/rankedwars?{query}")
        if ranked_error:
            lookup_errors.append(ranked_error)
        elif ranked_payload is not None:
            lookup_ok = True
            found = find_active_ranked_opponent(ranked_payload, own_id)
            sources_checked.append("faction/rankedwars")

    if not found:
        legacy_query = urlencode({"selections": "rankedwars", "key": api_key})
        legacy_payload, legacy_error = fetch_json(f"{TORN_V1}/faction/?{legacy_query}")
        if legacy_error:
            lookup_errors.append(legacy_error)
        elif legacy_payload is not None:
            lookup_ok = True
            found = find_active_ranked_opponent(legacy_payload, own_id)
            sources_checked.append("v1 faction rankedwars")

    if found:
        if own_error:
            lookup_errors.append(f"Could not read your faction details: {own_error}")
        return {
            "ok": True, "lookup_ok": lookup_ok, "own_id": own_id, "own_name": own_name,
            "opponent_id": found["id"], "opponent_name": found["name"], "war_id": found["war_id"],
            "source": sources_checked[-1] if sources_checked else "faction war data", "error": None,
        }

    if not own_id:
        message = "Could not identify the faction attached to this API key, so Owchowch personal war room can't determine which war faction is the opponent."
        if own_error:
            message += f" {own_error}"
        return {
            "ok": False, "lookup_ok": False, "own_id": own_id, "own_name": own_name,
            "opponent_id": "", "opponent_name": "", "war_id": "", "source": "", "error": message,
        }
    if lookup_ok:
        message = "No active ranked war was found for your API key's faction."
    else:
        message = "Could not read active ranked-war data. Check that your API key has faction wars/rankedwars, faction members, and user faction access."
        if lookup_errors:
            message += " " + " · ".join(lookup_errors[-3:])
    return {
        "ok": False, "lookup_ok": lookup_ok, "own_id": own_id, "own_name": own_name,
        "opponent_id": "", "opponent_name": "", "war_id": "", "source": "", "error": message,
    }


class WarroomHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, {"ok": True, "service": "torn-warroom-local-bridge"})
            return
        if path not in STATIC_PATHS:
            self._send_json(404, {"error": "Not found"})
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/torn-sync":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 20_000:
            self._send_json(400, {"error": "Invalid request size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Expected JSON request body"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "Expected a JSON object"})
            return
        api_key = str(body.get("apiKey", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", api_key):
            self._send_json(400, {"error": "API key format is not valid"})
            return

        # Resolve the active opponent and fetch its roster while reading the key owner's chain.
        discovery, chain_result = None, None
        with ThreadPoolExecutor(max_workers=2) as pool:
            war_future = pool.submit(discover_opponent, api_key)
            chain_future = pool.submit(get_chain, api_key)
            discovery = war_future.result()
            chain, chain_error = chain_future.result()

        opponent_id = discovery.get("opponent_id", "")
        members, members_error = None, None
        if opponent_id:
            members, members_error = get_members(opponent_id, api_key)

        self._send_json(200, {
            "warFound": bool(opponent_id),
            "warLookupOk": bool(discovery.get("lookup_ok")),
            "warError": discovery.get("error"),
            "warSource": discovery.get("source", ""),
            "ownFactionId": discovery.get("own_id", ""),
            "ownFactionName": discovery.get("own_name", ""),
            "factionId": opponent_id,
            "factionName": discovery.get("opponent_name", ""),
            "warId": discovery.get("war_id", ""),
            "syncedAt": int(time.time()),
            "members": members,
            "membersError": members_error,
            "chain": chain,
            "chainError": chain_error,
        })

    def _send_json(self, status: int, payload: dict):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # Log only method/path/status. API keys arrive in POST bodies and are never logged.
    def log_message(self, format, *args):
        super().log_message(format, *args)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    address = ("0.0.0.0", port)
    print(f"Owchowch personal war room listening on http://0.0.0.0:{port}")
    ThreadingHTTPServer(address, WarroomHandler).serve_forever()
