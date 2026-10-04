"""Spotify catalog search (client-credentials flow) and track reference parsing."""
import base64
import json
import os
import re
import stat
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from .player import BridgeError

CONFIG_PATH = os.path.expanduser("~/.config/walkman-bridge/spotify.json")
_ID = r"[A-Za-z0-9]{22}"
# Labels that change which recording it is. Remaster/mono/stereo are not listed:
# they are the normal catalog form of old songs and must not force a clarification.
_DISTINCT = r"live|cover|karaoke|instrumental|acoustic|remix|demo|tribute|session"
_VERSION_SUFFIX = re.compile(
    r"(?:\(|\[|\s-\s)\s*([^)\]]*?(?:live|remaster|remix|cover|karaoke|instrumental|acoustic|"
    r"demo|mono|stereo|edit|version|session)[^)\]]*?)\s*(?:\)|\])?\s*$", re.I)


def norm(s):
    """Casefold, strip Latin accents and punctuation. Leaves CJK text intact."""
    out = []
    for ch in unicodedata.normalize("NFKD", s or ""):
        if unicodedata.category(ch) == "Mn" and out and "LATIN" in unicodedata.name(out[-1], ""):
            continue
        out.append(ch)
    s = unicodedata.normalize("NFKC", "".join(out)).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def parse_ref(ref):
    """Return a spotify:{track,playlist,album}: URI for a URI or an open.spotify.com link, else raise."""
    ref = (ref or "").strip()
    m = re.fullmatch(rf"spotify:(track|playlist|album):({_ID})", ref)
    if m:
        return ref
    u = urllib.parse.urlparse(ref)
    if u.scheme == "https" and u.hostname == "open.spotify.com":
        m = re.fullmatch(rf"(?:/intl-[a-z]{{2,3}})?/(track|playlist|album)/({_ID})", u.path)
        if m:
            return f"spotify:{m.group(1)}:{m.group(2)}"
    raise BridgeError("INVALID_REF", "That is not a Spotify track, playlist or album link.")


def split_version(title):
    """'Yesterday - Remastered 2009' -> ('Yesterday', 'Remastered 2009')."""
    m = _VERSION_SUFFIX.search(title)
    if not m:
        return title, ""
    return title[:m.start()].strip(), m.group(1).strip()


def build_candidate(item):
    title, version = split_version(item["name"])
    album = (item.get("album") or {}).get("name", "")
    if not version and re.search(_DISTINCT, album, re.I):
        version = album  # e.g. "Live at Wembley" as album name
    return {"uri": item["uri"], "title": item["name"], "base_title": title,
            "artists": [a["name"] for a in item.get("artists", [])],
            "version": version, "album": album,
            "available": item.get("is_playable", True)}


def is_clear_match(c, title, artist, version):
    """True when this candidate is safe to play without asking the user."""
    if not artist or norm(c["base_title"]) != norm(title):
        return False
    if not any(norm(artist) == norm(a) or norm(artist) in norm(a) for a in c["artists"]):
        return False
    if version:
        return norm(version) in norm(c["version"])
    return not re.search(_DISTINCT, c["version"], re.I)


def load_config(path=CONFIG_PATH):
    cid, secret, market = (os.environ.get("SPOTIFY_CLIENT_ID"),
                           os.environ.get("SPOTIFY_CLIENT_SECRET"),
                           os.environ.get("SPOTIFY_MARKET"))
    if not (cid and secret):
        try:
            if os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise BridgeError("SEARCH_AUTH_REQUIRED",
                                  f"Spotify search config {path} must be owner-only (chmod 600).")
            with open(path) as f:
                cfg = json.load(f)
            cid, secret = cfg["client_id"], cfg["client_secret"]
            market = market or cfg.get("market")
        except (OSError, ValueError, KeyError):
            raise BridgeError("SEARCH_AUTH_REQUIRED",
                              "Spotify search credentials are not set up on the Walkman.")
    return cid, secret, market or "DE"


def urllib_http(method, url, headers, data):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except (urllib.error.URLError, OSError):
        raise BridgeError("SEARCH_UNREACHABLE", "Could not reach Spotify search.", True)


class Catalog:
    def __init__(self, client_id, client_secret, market="DE", http=urllib_http):
        self.cid, self.secret, self.market, self._http = client_id, client_secret, market, http
        self._token = None

    def _get_token(self):
        auth = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        status, _, body = self._http(
            "POST", "https://accounts.spotify.com/api/token",
            {"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"},
            b"grant_type=client_credentials")
        if status != 200:
            raise BridgeError("SEARCH_AUTH_REQUIRED", "Spotify rejected the search credentials.")
        self._token = json.loads(body)["access_token"]

    def search(self, title, artist=None, limit=5):
        if self._token is None:
            self._get_token()
        title, artist = title.replace('"', " "), (artist or "").replace('"', " ")
        q = f'track:"{title}"' + (f' artist:"{artist}"' if artist else "")
        url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
            {"q": q, "type": "track", "limit": limit, "market": self.market})
        status, headers, body = self._http("GET", url, {"Authorization": "Bearer " + self._token}, None)
        if status == 429:
            raise BridgeError("RATE_LIMITED",
                              f"Spotify search is rate limited; retry in {headers.get('Retry-After', '?')} s.", True)
        if status in (401, 403):
            raise BridgeError("SEARCH_AUTH_REQUIRED", "Spotify search access was refused.")
        if status != 200:
            raise BridgeError("SEARCH_UNREACHABLE", f"Spotify search failed (HTTP {status}).", True)
        items = json.loads(body).get("tracks", {}).get("items", [])
        cands = [build_candidate(i) for i in items if i]
        return [c for c in cands if c["available"]]
