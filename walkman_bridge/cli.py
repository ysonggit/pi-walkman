"""walkmanctl: play / control / status. One JSON object on stdout; exit 0 even for refusals."""
import argparse
import json
import sys

from . import catalog, store
from .player import BridgeError, Player


def _public(c, n):
    return {"id": n, "uri": c["uri"], "title": c["title"], "artists": c["artists"],
            "version": c["version"], "album": c["album"]}


def _resolve_pick(pick):
    """A candidate pick is a 1-based index or URI from the last search, still within its TTL."""
    cands = store.load_candidates()
    for i, c in enumerate(cands, 1):
        if pick == str(i) or pick == c["uri"]:
            return c["uri"]
    raise BridgeError("CANDIDATE_EXPIRED", "That choice has expired. Search again.")


def cmd_play(args, player, make_catalog):
    if args.candidate:
        uri = _resolve_pick(args.candidate)
    elif args.uri or args.link:
        uri = catalog.parse_ref(args.uri or args.link)
    elif args.title:
        cands = make_catalog().search(args.title, args.artist)
        if not cands:
            raise BridgeError("NO_MATCH", "No playable match was found. Nothing was changed.")
        clear = [c for c in cands if catalog.is_clear_match(c, args.title, args.artist, args.version)]
        if not clear:
            store.save_candidates(cands)
            return {"confirmation": "needs_choice",
                    "message": "Several recordings match. Ask which one, then call play --candidate.",
                    "candidates": [_public(c, i) for i, c in enumerate(cands, 1)]}
        uri = clear[0]["uri"]
    else:
        raise BridgeError("BAD_REQUEST", "play needs --title, --uri, --link or --candidate.")
    with store.mutation_lock():
        return player.play(uri)


def cmd_control(args, player, _):
    with store.mutation_lock():
        return {"pause": player.pause, "resume": player.resume,
                "next": lambda: player.skip("next"),
                "previous": lambda: player.skip("prev")}[args.action]()


def cmd_status(args, player, _):
    return player.status()


def build_parser():
    p = argparse.ArgumentParser(prog="walkmanctl")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("play")
    pl.add_argument("--title")
    pl.add_argument("--artist")
    pl.add_argument("--version", help="e.g. live, acoustic")
    pl.add_argument("--uri")
    pl.add_argument("--link")
    pl.add_argument("--candidate", help="index or URI from the last needs_choice result")
    ct = sub.add_parser("control")
    ct.add_argument("action", choices=["pause", "resume", "next", "previous"])
    sub.add_parser("status")
    return p


def main(argv=None, player=None, make_catalog=None, out=None):
    out = out or sys.stdout
    player = player or Player()
    make_catalog = make_catalog or (lambda: catalog.Catalog(*catalog.load_config()))
    handlers = {"play": cmd_play, "control": cmd_control, "status": cmd_status}
    try:
        args = build_parser().parse_args(argv)
        result = {"ok": True, **handlers[args.cmd](args, player, make_catalog)}
    except BridgeError as e:
        result = {"ok": False, "code": e.code, "message": e.message, "retryable": e.retryable}
    json.dump(result, out, ensure_ascii=False)
    out.write("\n")
    return 0
