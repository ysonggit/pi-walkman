import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from walkman_bridge import catalog, cli, store
from walkman_bridge.player import Player

URI = "spotify:track:" + "a" * 22
URI2 = "spotify:track:" + "b" * 22


class FakeGoLibrespot(HTTPServer):
    """Just enough of go-librespot's API. `start_works=False` simulates HTTP 200 with no playback."""

    def __init__(self, start_works=True):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.start_works, self.calls = start_works, []
        self.st = {"track": None, "paused": False, "stopped": True, "buffering": False}
        self.pos = 0
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            s = self.server
            if self.path == "/auth/code":
                self.send_response(404); self.end_headers(); return
            if s.st["track"] and not s.st["paused"] and not s.st["stopped"]:
                s.pos += 1000
            t = s.st["track"]
            self._send({**s.st, "track": t and {**t, "position": s.pos}})

        def do_POST(self):
            s = self.server
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            s.calls.append((self.path, body))
            if self.path == "/player/play" and s.start_works:
                s.pos = 0
                is_ctx = not body["uri"].startswith("spotify:track:")
                s.st.update(track=self._track(URI if is_ctx else body["uri"]), paused=False,
                            stopped=False, context_uri=body["uri"] if is_ctx else None)
            elif self.path == "/player/pause":
                s.st["paused"] = True
            elif self.path == "/player/resume":
                s.st["paused"] = False
            elif self.path == "/player/next":
                s.pos = 0
                s.st["track"] = self._track(URI2)
            self._send({})

        @staticmethod
        def _track(uri):
            return {"uri": uri, "name": "Song " + uri[-3:], "artist_names": ["Artist"],
                    "album_name": "Album", "duration": 200000}


def item(name, artist, uri=URI, album="Album", playable=True):
    return {"name": name, "uri": uri, "album": {"name": album},
            "artists": [{"name": artist}], "is_playable": playable}


def fake_http(items, search_status=200):
    def http(method, url, headers, data):
        if "accounts.spotify.com" in url:
            return 200, {}, b'{"access_token": "t"}'
        return search_status, {"Retry-After": "7"}, json.dumps({"tracks": {"items": items}}).encode()
    return http


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WALKMAN_STATE_DIR"] = self.tmp.name
        self.srv = FakeGoLibrespot()
        self.player = Player(f"http://127.0.0.1:{self.srv.server_port}", sleep=lambda s: None)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def run_cli(self, argv, items=(), search_status=200, player=None):
        out = io.StringIO()
        mk = lambda: catalog.Catalog("id", "secret", http=fake_http(list(items), search_status))
        cli.main(argv, player or self.player, mk, out)
        return json.loads(out.getvalue())

    # --- play by reference
    def test_play_uri_confirms_observed_playback(self):
        r = self.run_cli(["play", "--uri", URI])
        self.assertTrue(r["ok"])
        self.assertEqual((r["confirmation"], r["state"], r["track"]["uri"]), ("confirmed", "playing", URI))

    def test_link_with_intl_prefix_and_tracking_query(self):
        r = self.run_cli(["play", "--link", f"https://open.spotify.com/intl-de/track/{'a'*22}?si=xyz"])
        self.assertEqual(r["track"]["uri"], URI)

    def test_playlist_link_plays_and_confirms_context(self):
        pl = "spotify:playlist:" + "c" * 22
        r = self.run_cli(["play", "--link", f"https://open.spotify.com/playlist/{'c'*22}?si=n7g"])
        self.assertEqual((r["confirmation"], r["context_uri"]), ("confirmed", pl))
        self.assertEqual(self.srv.calls[0], ("/player/play", {"uri": pl}))

    def test_bad_links_rejected_without_touching_player(self):
        for bad in ["https://evil.example/track/" + "a" * 22,
                    "https://open.spotify.com/artist/" + "a" * 22,
                    "http://open.spotify.com/track/" + "a" * 22,
                    "spotify:artist:" + "a" * 22]:
            r = self.run_cli(["play", "--link", bad])
            self.assertEqual(r["code"], "INVALID_REF", bad)
        self.assertEqual(self.srv.calls, [])

    def test_http_ok_but_no_playback_is_unconfirmed_not_success(self):
        self.srv.start_works = False
        ticks = iter(range(0, 1000))
        self.player._clock = lambda: next(ticks)  # each poll advances 1 s, so the 10 s budget ends fast
        r = self.run_cli(["play", "--uri", URI])
        self.assertTrue(r["ok"])
        self.assertEqual(r["confirmation"], "unconfirmed")

    # --- play by name
    def test_clear_match_plays_without_asking(self):
        r = self.run_cli(["play", "--title", "Yesterday", "--artist", "The Beatles"],
                         [item("Yesterday - Remastered 2009", "The Beatles")])
        self.assertEqual(r["confirmation"], "confirmed")

    def test_no_artist_asks_and_leaves_playback_untouched(self):
        r = self.run_cli(["play", "--title", "Yesterday"],
                         [item("Yesterday", "The Beatles"), item("Yesterday", "Boyz II Men", URI2)])
        self.assertEqual(r["confirmation"], "needs_choice")
        self.assertEqual(len(r["candidates"]), 2)
        self.assertEqual(self.srv.calls, [])

    def test_live_version_is_not_picked_silently(self):
        r = self.run_cli(["play", "--title", "Yesterday", "--artist", "The Beatles"],
                         [item("Yesterday - Live", "The Beatles")])
        self.assertEqual(r["confirmation"], "needs_choice")

    def test_requested_version_picks_that_recording(self):
        r = self.run_cli(["play", "--title", "Yesterday", "--artist", "The Beatles", "--version", "live"],
                         [item("Yesterday", "The Beatles", URI2), item("Yesterday - Live", "The Beatles", URI)])
        self.assertEqual(r["track"]["uri"], URI)

    def test_pick_after_choice_by_index_then_expiry(self):
        self.run_cli(["play", "--title", "Yesterday"], [item("Yesterday", "A"), item("Yesterday", "B", URI2)])
        r = self.run_cli(["play", "--candidate", "2"])
        self.assertEqual(r["track"]["uri"], URI2)
        os.remove(os.path.join(self.tmp.name, "last_search.json"))
        self.assertEqual(self.run_cli(["play", "--candidate", "1"])["code"], "CANDIDATE_EXPIRED")

    def test_stale_candidates_expire(self):
        store.save_candidates([{"uri": URI}], now=lambda: 0)
        self.assertEqual(self.run_cli(["play", "--candidate", URI])["code"], "CANDIDATE_EXPIRED")

    def test_arbitrary_uri_not_accepted_as_candidate(self):
        self.assertEqual(self.run_cli(["play", "--candidate", URI])["code"], "CANDIDATE_EXPIRED")

    def test_no_match_and_unplayable_leave_playback_alone(self):
        r = self.run_cli(["play", "--title", "zzz", "--artist", "q"], [item("zzz", "q", playable=False)])
        self.assertEqual(r["code"], "NO_MATCH")
        self.assertEqual(self.srv.calls, [])

    def test_search_failures_map_to_codes(self):
        self.assertEqual(self.run_cli(["play", "--title", "x"], search_status=429)["code"], "RATE_LIMITED")
        self.assertEqual(self.run_cli(["play", "--title", "x"], search_status=403)["code"], "SEARCH_AUTH_REQUIRED")

    def test_chinese_and_accented_titles(self):
        r = self.run_cli(["play", "--title", "晴天", "--artist", "周杰伦"], [item("晴天", "周杰伦")])
        self.assertEqual(r["confirmation"], "confirmed")
        self.assertEqual(catalog.norm("Beyoncé"), "beyonce")

    # --- controls
    def test_pause_resume_next(self):
        self.run_cli(["play", "--uri", URI])
        self.assertEqual(self.run_cli(["control", "pause"])["state"], "paused")
        self.assertEqual(self.run_cli(["control", "resume"])["state"], "playing")
        r = self.run_cli(["control", "next"])
        self.assertEqual((r["confirmation"], r["track"]["uri"]), ("confirmed", URI2))
        self.assertEqual([c for c in self.srv.calls if c[0] == "/player/next"].__len__(), 1)

    def test_status(self):
        r = self.run_cli(["status"])
        self.assertEqual((r["ok"], r["state"], r["track"]), (True, "stopped", None))

    def test_player_offline(self):
        dead = Player("http://127.0.0.1:1", sleep=lambda s: None)
        r = self.run_cli(["status"], player=dead)
        self.assertEqual((r["ok"], r["code"], r["retryable"]), (False, "PLAYER_OFFLINE", True))

    def test_no_credentials_reported(self):
        out = io.StringIO()
        os.environ.pop("SPOTIFY_CLIENT_ID", None)
        old = catalog.CONFIG_PATH
        try:
            def mk():
                return catalog.Catalog(*catalog.load_config(os.path.join(self.tmp.name, "none.json")))
            cli.main(["play", "--title", "x"], self.player, mk, out)
        finally:
            catalog.CONFIG_PATH = old
        self.assertEqual(json.loads(out.getvalue())["code"], "SEARCH_AUTH_REQUIRED")

    def test_group_readable_config_refused(self):
        p = os.path.join(self.tmp.name, "s.json")
        with open(p, "w") as f:
            json.dump({"client_id": "a", "client_secret": "b"}, f)
        os.chmod(p, 0o644)
        os.environ.pop("SPOTIFY_CLIENT_ID", None)
        with self.assertRaises(Exception) as cm:
            catalog.load_config(p)
        self.assertEqual(cm.exception.code, "SEARCH_AUTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
