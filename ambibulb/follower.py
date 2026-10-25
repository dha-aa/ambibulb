"""Standalone on-screen color panel (no bulbs) -- a quick way to preview the
color the engine would send. Uses tkinter (stdlib)."""

from __future__ import annotations

import argparse
import sys
import tkinter as tk

import mss

from . import color


class ColorFollower:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._sct = (mss.MSS if hasattr(mss, "MSS") else mss.mss)()
        self.region = self._region(args)
        self.cur = [0.0, 0.0, 0.0]
        self.interval_ms = max(1, int(1000 / max(1, args.fps)))

        self.root = tk.Tk()
        self.root.title("ambibulb follower")
        self.root.geometry("480x320")
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bg="#000")
        self.canvas.pack(fill="both", expand=True)
        self.label = self.canvas.create_text(10, 10, anchor="nw", text="",
                                              font=("Menlo", 14), fill="#fff")
        self.fullscreen = False
        self.root.bind("<Key-q>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<Key-f>", lambda e: self._toggle_fs())
        self.root.bind("<F11>", lambda e: self._toggle_fs())

    def _region(self, args):
        if args.region:
            l, t, w, h = args.region
            return {"left": l, "top": t, "width": w, "height": h}
        mons = self._sct.monitors
        idx = args.monitor if 0 <= args.monitor < len(mons) else 1
        return mons[idx]

    def _toggle_fs(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def _sample(self):
        shot = self._sct.grab(self.region)
        if color.HAVE_PIL:
            img = color.Image.frombytes("RGB", shot.size, shot.rgb)
            if self.args.mode in ("dominant", "vivid"):
                method = "vivid" if self.args.mode == "vivid" else "vibrance"
                return color.zone_colors(img, 1, method)[0]
            return color.average_color_pil(img)
        return color.average_color(shot.bgra, shot.width, shot.height, step=8)

    def _tick(self):
        try:
            target = self._sample()
        except Exception as exc:
            self.canvas.itemconfig(self.label, text=f"capture error: {exc}")
            self.root.after(self.interval_ms, self._tick)
            return
        a = self.args.smooth
        for i in range(3):
            self.cur[i] += (target[i] - self.cur[i]) * a
        r, g, b = (int(round(c)) for c in self.cur)
        hexc = f"#{r:02x}{g:02x}{b:02x}"
        self.canvas.configure(bg=hexc)
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        self.canvas.itemconfig(self.label, text=f"{hexc}  rgb({r},{g},{b})",
                               fill="#000" if luma > 140 else "#fff")
        self.root.after(self.interval_ms, self._tick)

    def run(self):
        self.root.after(0, self._tick)
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ambibulb-follow",
                                description="On-screen color panel (no bulbs).")
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--region", type=int, nargs=4, metavar=("L", "T", "W", "H"))
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--smooth", type=float, default=0.2)
    p.add_argument("--mode", choices=("average", "dominant", "vivid"),
                   default="average")
    a = p.parse_args(argv)
    a.smooth = min(1.0, max(0.01, a.smooth))
    ColorFollower(a).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
