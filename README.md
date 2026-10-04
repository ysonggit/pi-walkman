# pi-walkman

Raspberry Pi as a Spotify Connect "Walkman": audio out of the 3.5mm jack, a cassette
tape on the 3.5" touchscreen whose reels spin while music plays.

- **go-librespot** (user service) makes the Pi appear as the Spotify device **Walkman**.
- **walkman.py** (pygame, desktop autostart) polls `http://127.0.0.1:3678/status` and draws the tape.
  Tape moves from the left reel to the right with track progress.
- Touch: left third = previous, middle = play/pause, right third = next.

## Install (on the Pi, no sudo)

```bash
scp walkman.py install.sh yang@groundmind.local:walkman/
ssh yang@groundmind.local walkman/install.sh
```

Then in the Spotify app on your phone (same Wi-Fi): Devices → **Walkman**.

## Preview without Spotify

```bash
python3 walkman.py --window --demo
```

## Troubleshooting

- `systemctl --user status go-librespot` / `journalctl --user -u go-librespot`
- No sound: check the sink in `~/.config/go-librespot/config.yml` against `wpctl status`;
  re-run with `SINK=<node.name> ./install.sh` for another output.
- Screen log: `/tmp/walkman.log` when started by hand.

## walkmanctl (bridge for voice/chat agents)

A stdlib-only CLI that talks to the same `:3678` API. It does not touch `walkman.py` or the player.
Every call prints one JSON line; `confirmation` is `confirmed` only after the player is seen playing
(position advancing), otherwise `unconfirmed`.

```bash
rsync -a --exclude __pycache__ walkman_bridge walkmanctl yang@groundmind.local:walkman/
walkman/walkmanctl status
walkman/walkmanctl play --title "Yesterday" --artist "The Beatles"   # plays, or returns needs_choice
walkman/walkmanctl play --candidate 2                                # pick from the last needs_choice
walkman/walkmanctl play --link https://open.spotify.com/{track|playlist|album}/<id>   # or --uri spotify:<kind>:<id>
walkman/walkmanctl control pause|resume|next|previous
```

Searching by name needs a Spotify developer app (client credentials, no user login). Put it in
`~/.config/walkman-bridge/spotify.json` with `chmod 600`:
`{"client_id": "...", "client_secret": "...", "market": "DE"}`.
A title with no artist, or a live/cover/karaoke match, returns `needs_choice` instead of guessing.

Tests: `python3 -m unittest discover -s tests`
