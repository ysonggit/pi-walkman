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
