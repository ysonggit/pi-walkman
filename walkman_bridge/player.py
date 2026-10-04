"""Client for the go-librespot HTTP API on localhost, with playback verification."""
import json
import socket
import time
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:3678"


class BridgeError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def normalize(status):
    """Reduce go-librespot's /status to {state, track}."""
    t = status.get("track")
    track = None
    if t:
        track = {"uri": t.get("uri"), "title": t.get("name"),
                 "artists": t.get("artist_names") or [], "album": t.get("album_name"),
                 "position_ms": t.get("position", 0), "duration_ms": t.get("duration", 0)}
    if not t or status.get("stopped"):
        state = "stopped"
    elif status.get("buffering"):
        state = "buffering"
    elif status.get("paused"):
        state = "paused"
    else:
        state = "playing"
    return {"state": state, "track": track, "context_uri": status.get("context_uri")}


class Player:
    def __init__(self, base=DEFAULT_API, timeout=3, sleep=time.sleep, clock=time.monotonic):
        self.base, self.timeout, self._sleep, self._clock = base, timeout, sleep, clock

    def _call(self, method, path, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            e.close()
            raise BridgeError("PLAYER_ERROR", f"Player rejected {path} (HTTP {e.code}).", e.code >= 500)
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            raise BridgeError("PLAYER_OFFLINE", "The Walkman player is not reachable.", True)
        return json.loads(raw) if raw else {}

    def status(self):
        # While Spotify login is pending /status blocks, so ask for the pairing code first.
        try:
            code = self._call("GET", "/auth/code").get("code")
        except BridgeError:
            code = None
        if code:
            raise BridgeError("AUTH_REQUIRED", "Spotify login is needed on the Walkman.")
        return normalize(self._call("GET", "/status"))

    def _wait(self, predicate, timeout):
        """Poll status until predicate(state) is true or timeout. Returns (ok, last_state)."""
        deadline = self._clock() + timeout
        state = self.status()
        while True:
            if predicate(state):
                return True, state
            if self._clock() >= deadline:
                return False, state
            self._sleep(0.5)
            state = self.status()

    def _advancing(self, uri, timeout, context=None):
        """True once `uri` (or a track of playlist/album `context`) plays and its position moves."""
        seen = {}

        def moving(state):
            tr = state["track"]
            if (state["state"] != "playing" or not tr or (uri and tr["uri"] != uri)
                    or (context and state["context_uri"] != context)):
                seen.clear()
                return False
            pos = tr["position_ms"]
            if "pos" in seen and pos > seen["pos"]:
                return True
            seen["pos"] = pos
            return False

        return self._wait(moving, timeout)

    def play(self, uri, timeout=10):
        self._call("POST", "/player/play", {"uri": uri})
        if uri.startswith("spotify:track:"):
            ok, state = self._advancing(uri, timeout)
        else:  # playlist or album: any of its tracks may start
            ok, state = self._advancing(None, timeout, context=uri)
        return {"confirmation": "confirmed" if ok else "unconfirmed", **state}

    def pause(self, timeout=3):
        self._call("POST", "/player/pause")
        ok, state = self._wait(lambda s: s["state"] == "paused", timeout)
        return {"confirmation": "confirmed" if ok else "unconfirmed", **state}

    def resume(self, timeout=5):
        self._call("POST", "/player/resume")
        ok, state = self._advancing(None, timeout)
        return {"confirmation": "confirmed" if ok else "unconfirmed", **state}

    def skip(self, direction, timeout=6):
        """direction: 'next' or 'prev'. Confirmed when the track changed or restarted."""
        before = self.status()["track"]

        def changed(state):
            tr = state["track"]
            if state["state"] != "playing" or not tr:
                return False
            if not before:
                return True
            return tr["uri"] != before["uri"] or tr["position_ms"] < before["position_ms"]

        self._call("POST", "/player/" + direction)
        ok, state = self._wait(changed, timeout)
        return {"confirmation": "confirmed" if ok else "unconfirmed", **state}
