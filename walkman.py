#!/usr/bin/env python3
"""Cassette-tape "now playing" screen for go-librespot (Spotify Connect).

Polls the go-librespot API and draws a cassette whose reels spin while a track
plays. Tape moves from the left reel to the right one as the song progresses.

Touch: left third = previous, middle = play/pause, right third = next.

  python3 walkman.py            # fullscreen, talks to go-librespot on :3678
  python3 walkman.py --window   # 480x320 window (desktop preview)
  python3 walkman.py --demo     # fake track, no go-librespot needed
"""
import argparse
import colorsys
import io
import json
import math
import os
import threading
import time
import urllib.request

import pygame
import pygame.gfxdraw

W, H = 480, 320
API = os.environ.get("WALKMAN_API", "http://127.0.0.1:3678")

# First existing path wins. Latin and CJK fonts are separate because the Pi's
# CJK fallback font has no Latin glyphs.
LATIN_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
CJK_FONTS = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
]

BG = (24, 22, 28)
SHELL = (52, 50, 58)
SHELL_EDGE = (78, 76, 86)
LABEL = (238, 230, 212)
INK = (40, 36, 44)
TAPE = (92, 56, 38)
WINDOW = (22, 20, 24)
HUB = (232, 232, 228)

# Geometry
LABEL_RECT = pygame.Rect(36, 24, 408, 196)
BAND_RECT = pygame.Rect(36, 98, 408, 104)
WIN_RECT = pygame.Rect(106, 110, 268, 72)
REEL_L, REEL_R = (174, 146), (306, 146)
HUB_R, PACK_MAX = 19, 62
TAPE_SPEED = 38.0  # px/s of tape surface; reel angular speed = speed / radius


# ---------------------------------------------------------------- player state

class Player:
    """Background poller for go-librespot's /status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._s = {"online": False, "active": False, "playing": False,
                   "name": "", "artist": "", "album": "", "cover": "", "pos": 0, "dur": 0,
                   "t": time.monotonic()}
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                s = {"online": True, "active": False, "playing": False, "pair": "", "cover": ""}
                # While waiting for spotify.com/pair, /status blocks, so ask for the code first.
                try:
                    with urllib.request.urlopen(API + "/auth/code", timeout=2) as a:
                        s["pair"] = json.loads(a.read())["code"]
                except Exception:
                    pass
                body = b""
                if not s["pair"]:
                    with urllib.request.urlopen(API + "/status", timeout=2) as r:
                        body = r.read() if r.status == 200 else b""
                if body:
                    st = json.loads(body)
                    tr = st.get("track")
                    if tr:
                        s.update(active=True,
                                 playing=not (st["paused"] or st["stopped"] or st["buffering"]),
                                 name=tr["name"], artist=", ".join(tr["artist_names"]),
                                 album=tr["album_name"], pos=tr["position"], dur=tr["duration"],
                                 cover=tr.get("album_cover_url") or "")
            except Exception:
                s = {"online": False, "active": False, "playing": False, "pair": "", "cover": ""}
            s["t"] = time.monotonic()
            with self._lock:
                self._s.update(s)
            self._wake.wait(1.0)
            self._wake.clear()

    def snapshot(self):
        with self._lock:
            s = dict(self._s)
        if s["playing"]:  # interpolate between polls
            s["pos"] = min(s["dur"], s["pos"] + (time.monotonic() - s["t"]) * 1000)
        return s

    def command(self, path):
        def send():
            try:
                req = urllib.request.Request(API + path, data=b"", method="POST")
                urllib.request.urlopen(req, timeout=3).close()
            except Exception as e:
                print("command failed:", path, e)
            self._wake.set()
        threading.Thread(target=send, daemon=True).start()


class DemoPlayer:
    """Fake player for previewing without Spotify."""

    TRACKS = [("Mixtape Side A", "The Demo Band", 95_000),
              ("夜空中最亮的星", "逃跑计划", 120_000)]

    def __init__(self):
        self.i, self.pos, self.playing, self.t = 0, 30_000, True, time.monotonic()

    def snapshot(self):
        now = time.monotonic()
        if self.playing:
            self.pos += (now - self.t) * 1000
        self.t = now
        name, artist, dur = self.TRACKS[self.i]
        if self.pos >= dur:
            self.command("/player/next")
            return self.snapshot()
        return {"online": True, "active": True, "playing": self.playing,
                "name": name, "artist": artist, "album": name, "pos": self.pos, "dur": dur}

    def command(self, path):
        if path == "/player/playpause":
            self.playing = not self.playing
        elif path in ("/player/next", "/player/prev"):
            self.i = (self.i + (1 if path.endswith("next") else -1)) % len(self.TRACKS)
            self.pos = 0


class CoverLoader:
    """Fetches the current album cover in the background; .img is None until loaded."""

    def __init__(self):
        self.url, self.img = "", None

    def want(self, url):
        if url == self.url:
            return
        self.url, self.img = url, None
        if url:
            threading.Thread(target=self._fetch, args=(url,), daemon=True).start()

    def _fetch(self, url):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                img = pygame.image.load(io.BytesIO(r.read()), "cover.jpg")
        except Exception as e:
            print("cover failed:", url, e)
            return
        if url == self.url:
            self.img = img


# ---------------------------------------------------------------- drawing

class MixedFont:
    """Renders text with a Latin font, switching to a CJK font for CJK characters."""

    def __init__(self, size):
        self.latin = self._load(LATIN_FONTS, size)
        self.cjk = self._load(CJK_FONTS, size) or self.latin
        self._cache = {}

    @staticmethod
    def _load(paths, size):
        for p in paths:
            if os.path.exists(p):
                return pygame.font.Font(p, size)
        return pygame.font.Font(None, size)

    def render(self, text, aa, color):
        key = (text, color)
        if key not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = self._render(text, aa, color)
        return self._cache[key]

    def _render(self, text, aa, color):
        runs = []  # [(font, str)]
        for ch in text:
            f = self.cjk if ord(ch) >= 0x2E80 else self.latin
            if runs and runs[-1][0] is f:
                runs[-1][1].append(ch)
            else:
                runs.append((f, [ch]))
        imgs = [f.render("".join(chs), aa, color) for f, chs in runs] or [self.latin.render("", aa, color)]
        h = max(i.get_height() for i in imgs)
        out = pygame.Surface((sum(i.get_width() for i in imgs), h), pygame.SRCALPHA)
        x = 0
        for i in imgs:
            out.blit(i, (x, h - i.get_height()))
            x += i.get_width()
        return out


def aa_circle(surf, color, center, r):
    x, y = int(center[0]), int(center[1])
    pygame.gfxdraw.filled_circle(surf, x, y, int(r), color)
    pygame.gfxdraw.aacircle(surf, x, y, int(r), color)


def fmt(ms):
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


class Cassette:
    def __init__(self):
        self.f_title = MixedFont(26)
        self.f_artist = MixedFont(18)
        self.f_small = MixedFont(14)
        self.f_side = MixedFont(40)
        self.angle_l = self.angle_r = 0.0
        self.scroll = 0.0
        self.flash = None  # (kind, until)
        self.cover = CoverLoader()
        self.label_key = None
        self.label = None
        self.shell = SHELL

    def build_label(self, album, img):
        """Label art, rebuilt only when the track's album or cover changes."""
        label = pygame.Surface(LABEL_RECT.size, pygame.SRCALPHA)
        lw, lh = LABEL_RECT.size
        if img:
            # Background: cover scaled to cover the label, blurred and darkened.
            side = max(lw, lh)
            big = pygame.transform.smoothscale(img, (side, side))
            bg = big.subsurface((0, (side - lh) // 2, lw, lh)).copy()
            bg = pygame.transform.smoothscale(bg, (lw // 9, lh // 9))
            bg = pygame.transform.smoothscale(bg, (lw, lh))
            bg.blit(self._shade(lw, lh), (0, 0))
            mask = pygame.Surface((lw, lh), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=8)
            label.blit(bg, (0, 0))
            label.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            # Sharp cover "sticker" top-left.
            pygame.draw.rect(label, (245, 242, 235), (8, 6, 66, 66), border_radius=3)
            label.blit(pygame.transform.smoothscale(img, (60, 60)), (11, 9))
            avg = pygame.transform.average_color(img)[:3]
            self.shell = tuple(int(s * 0.7 + a * 0.3) for s, a in zip(SHELL, avg))
        else:
            hue = (sum(map(ord, album or "walkman")) * 37 % 360) / 360
            band_c = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.85))
            pygame.draw.rect(label, LABEL, label.get_rect(), border_radius=8)
            band = BAND_RECT.move(-LABEL_RECT.x, -LABEL_RECT.y)
            pygame.draw.rect(label, band_c, band)
            pygame.draw.rect(label, tuple(max(0, c - 40) for c in band_c), band.inflate(0, -84).move(0, 36))
            self.shell = SHELL
        hole = WIN_RECT.move(-LABEL_RECT.x, -LABEL_RECT.y)
        pygame.draw.rect(label, (0, 0, 0, 0), hole, border_radius=10)
        return label

    @staticmethod
    def _shade(w, h):
        """Darkening overlay, heavier at the top so title text stays readable."""
        shade = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(h):
            shade.fill((0, 0, 0, int(125 - 85 * y / h)), (0, y, w, 1))
        return shade

    def update(self, s, dt):
        p = s["pos"] / s["dur"] if s["dur"] else 0.0
        self.r_l, self.r_r = self.pack_radii(p if s["active"] else 0.0)
        if s["playing"]:
            self.angle_l += TAPE_SPEED * dt / self.r_l
            self.angle_r += TAPE_SPEED * dt / self.r_r
        self.scroll += dt * 40

    @staticmethod
    def pack_radii(p):
        # Tape area is conserved: r_l^2 + r_r^2 is constant.
        area = PACK_MAX ** 2 - HUB_R ** 2
        return (math.sqrt(HUB_R ** 2 + area * (1 - p)),
                math.sqrt(HUB_R ** 2 + area * p))

    def draw(self, surf, s):
        surf.fill(BG)
        # shell
        pygame.draw.rect(surf, SHELL_EDGE, (12, 8, 456, 304), border_radius=20)
        pygame.draw.rect(surf, self.shell, (16, 12, 448, 296), border_radius=18)
        for x, y in ((30, 26), (450, 26), (30, 294), (450, 294)):
            self.screw(surf, (x, y))

        # tape packs + hubs, seen through the window
        win = surf.subsurface(WIN_RECT)
        win.fill(WINDOW)
        ox, oy = WIN_RECT.topleft
        for (cx, cy), r, a in ((REEL_L, self.r_l, self.angle_l), (REEL_R, self.r_r, self.angle_r)):
            c = (cx - ox, cy - oy)
            aa_circle(win, TAPE, c, r)
            aa_circle(win, (70, 42, 30), c, HUB_R + 3)
            self.hub(win, c, a)
        # glass sheen + window frame
        sheen = pygame.Surface(WIN_RECT.size, pygame.SRCALPHA)
        pygame.draw.polygon(sheen, (255, 255, 255, 18), [(40, 0), (90, 0), (40, 72), (-10, 72)])
        win.blit(sheen, (0, 0))

        # label (drawn around the window)
        self.cover.want(s.get("cover", "") if s["active"] else "")
        img = self.cover.img
        key = (s["album"], self.cover.url if img else None)
        if key != self.label_key:
            self.label_key, self.label = key, self.build_label(s["album"], img)
        surf.blit(self.label, LABEL_RECT)
        pygame.draw.rect(surf, (30, 28, 32), WIN_RECT, width=3, border_radius=10)

        # text
        if s["active"]:
            title, sub = s["name"], s["artist"]
        elif s.get("pair"):
            title, sub = "PAIR: " + s["pair"], "enter at spotify.com/pair"
        elif s["online"]:
            title, sub = "WALKMAN", "Spotify → Devices → Walkman"
        else:
            title, sub = "WALKMAN", "starting…"
        if img:  # text beside the cover sticker, light on dark art
            self.marquee(surf, self.f_title, title, (250, 248, 242), pygame.Rect(120, 34, 312, 30), left=True)
            self.marquee(surf, self.f_artist, sub, (215, 210, 200), pygame.Rect(120, 66, 312, 24), left=True)
        else:
            self.marquee(surf, self.f_title, title, INK, pygame.Rect(52, 34, 376, 30))
            self.marquee(surf, self.f_artist, sub, (90, 84, 96), pygame.Rect(52, 66, 376, 24))
        side = self.f_side.render("A", True, LABEL)
        surf.blit(side, side.get_rect(center=(72, 146)))
        if s["active"]:
            t = self.f_small.render(f"{fmt(s['pos'])}", True, LABEL)
            surf.blit(t, t.get_rect(center=(410, 136)))
            t = self.f_small.render(f"{fmt(s['dur'])}", True, LABEL)
            surf.blit(t, t.get_rect(center=(410, 156)))
            state = "PLAY" if s["playing"] else "PAUSE"
        else:
            state = "STOP"
        t = self.f_small.render(state, True, (200, 196, 190) if img else (120, 112, 124))
        surf.blit(t, t.get_rect(center=(240, 208)))

        self.head_area(surf, s)
        self.draw_flash(surf)

    def hub(self, surf, c, angle):
        aa_circle(surf, HUB, c, HUB_R)
        aa_circle(surf, WINDOW, c, 11)
        for k in range(6):  # six teeth pointing inward
            a = angle + k * math.pi / 3
            dx, dy = math.cos(a), math.sin(a)
            px, py = -dy, dx
            pts = [(c[0] + dx * r + px * w, c[1] + dy * r + py * w)
                   for r, w in ((12, 2.5), (6, 1.5), (6, -1.5), (12, -2.5))]
            pygame.gfxdraw.aapolygon(surf, pts, HUB)
            pygame.gfxdraw.filled_polygon(surf, pts, HUB)

    def screw(self, surf, c):
        aa_circle(surf, (110, 108, 116), c, 6)
        pygame.draw.line(surf, (60, 58, 64), (c[0] - 4, c[1] - 4), (c[0] + 4, c[1] + 4), 2)

    def head_area(self, surf, s):
        pts = [(118, 308), (140, 238), (340, 238), (362, 308)]
        pygame.draw.polygon(surf, (44, 42, 50), pts)
        pygame.draw.aalines(surf, SHELL_EDGE, False, pts)
        # tape running along the bottom
        pygame.draw.line(surf, TAPE, (130, 296), (350, 296), 3)
        for x in (172, 308):
            aa_circle(surf, (20, 18, 22), (x, 270), 9)
        pygame.draw.rect(surf, (20, 18, 22), (222, 258, 36, 22), border_radius=3)
        if s["playing"]:  # tiny tape-motion ticks
            off = int(self.scroll) % 12
            for x in range(134 + off, 348, 12):
                pygame.draw.line(surf, (140, 92, 64), (x, 296), (x + 3, 296), 1)

    def marquee(self, surf, font, text, color, rect, left=False):
        img = font.render(text, True, color)
        if img.get_width() <= rect.w:
            pos = rect.topleft if left else img.get_rect(midtop=(rect.centerx, rect.y))
            surf.blit(img, pos)
            return
        gap = 60
        span = img.get_width() + gap
        x = -int(self.scroll) % span
        clip = surf.get_clip()
        surf.set_clip(rect)
        surf.blit(img, (rect.x - x, rect.y))
        surf.blit(img, (rect.x - x + span, rect.y))
        surf.set_clip(clip)

    def tap(self, kind):
        self.flash = (kind, time.monotonic() + 0.6)

    def draw_flash(self, surf):
        if not self.flash or time.monotonic() > self.flash[1]:
            self.flash = None
            return
        kind = self.flash[0]
        ov = pygame.Surface((120, 80), pygame.SRCALPHA)
        pygame.draw.rect(ov, (0, 0, 0, 170), ov.get_rect(), border_radius=14)
        w = (255, 255, 255)
        if kind == "playpause":
            pygame.draw.polygon(ov, w, [(28, 22), (28, 58), (54, 40)])
            pygame.draw.rect(ov, w, (70, 22, 8, 36))
            pygame.draw.rect(ov, w, (84, 22, 8, 36))
        else:
            d = 1 if kind == "next" else -1
            for bx in (48, 72):
                x0 = bx if d > 0 else 120 - bx
                pygame.draw.polygon(ov, w, [(x0 - 12 * d, 22), (x0 - 12 * d, 58), (x0 + 12 * d, 40)])
        surf.blit(ov, ov.get_rect(center=(W // 2, H // 2)))


# ---------------------------------------------------------------- main

def on_active_vt():
    """False when our X session sits on a VT that isn't on screen (then we idle)."""
    vt = os.environ.get("XDG_VTNR")
    try:
        with open("/sys/class/tty/tty0/active") as f:
            return not vt or f.read().strip() == "tty" + vt
    except OSError:
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true", help="480x320 window instead of fullscreen")
    ap.add_argument("--flip", action="store_true", help="rotate the picture 180 degrees (screen mounted upside down)")
    ap.add_argument("--demo", action="store_true", help="fake playback, no go-librespot")
    args = ap.parse_args()

    pygame.display.init()  # not pygame.init(): the screen needs no audio device
    pygame.font.init()
    if args.window:
        screen = pygame.display.set_mode((W, H))
    else:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)
    pygame.display.set_caption("Walkman")
    frame = pygame.Surface((W, H))

    player = DemoPlayer() if args.demo else Player()
    tape = Cassette()
    clock = pygame.time.Clock()

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE):
                return
            if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                sw = screen.get_width()
                x = sw - ev.pos[0] if args.flip else ev.pos[0]
                kind = "prev" if x < sw / 3 else "next" if x > sw * 2 / 3 else "playpause"
                player.command("/player/" + kind)
                tape.tap(kind)

        if not on_active_vt():
            clock.tick(1)
            continue
        s = player.snapshot()
        dt = clock.tick(20 if s["playing"] or tape.flash else 6) / 1000
        tape.update(s, dt)
        tape.draw(frame, s)
        out = pygame.transform.flip(frame, True, True) if args.flip else frame
        if screen.get_size() == (W, H):
            screen.blit(out, (0, 0))
        else:
            screen.blit(pygame.transform.smoothscale(out, screen.get_size()), (0, 0))
        pygame.display.flip()


if __name__ == "__main__":
    main()
