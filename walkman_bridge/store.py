"""Small on-disk state: the last search results (so a pick can be validated) and a mutation lock."""
import contextlib
import fcntl
import json
import os
import time

from .player import BridgeError

TTL = 600  # seconds a search result stays pickable


def state_dir():
    d = os.environ.get("WALKMAN_STATE_DIR") or os.path.expanduser("~/.cache/walkman-bridge")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def save_candidates(cands, now=time.time):
    path = os.path.join(state_dir(), "last_search.json")
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"at": now(), "candidates": cands}, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_candidates(now=time.time):
    try:
        with open(os.path.join(state_dir(), "last_search.json")) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data["candidates"] if now() - data.get("at", 0) <= TTL else []


@contextlib.contextmanager
def mutation_lock(wait=10):
    """Serialize play/pause/skip so two requests never interleave."""
    with open(os.path.join(state_dir(), "mutation.lock"), "w") as f:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BridgeError("BUSY", "Another Walkman request is still running.", True)
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
