"""ADHD-friendly printable planners rebuilt to sell (CCB-1): three undated editions, 24+ full pages each.

  energy          Energy-Based Daily Planner: plan tasks by your energy, not the clock
  time_blindness  Time-Blindness Routine Planner: time anchors, routines, guess-vs-real time
  focus           Focus & Brain-Dump Sheets: brain dumps, 15-minute focus blocks, priority grids

Design rules (from the product checks "very plain, bottom 30-40% empty, labels cut off"):
one accent colour + a header band on every page, every page filled down to the footer, every label
fitted to its box (never cut), page numbers, a How to use page, undated pages, A4 + US Letter.
The same layout code draws the PDF pages and the PNG previews, so the previews show real pages.
Plain organising tool: no medical, health or productivity promises.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

try:
    from PIL import Image, ImageDraw
    HAS_PILLOW = True
except ImportError:  # pragma: no cover - CI installs Pillow
    Image = ImageDraw = None
    HAS_PILLOW = False

import pdf_design
from pdf_design import MARGIN, fit_text, wrap_text

FORMAT = "adhd_planner"
KEYWORDS = [
    "energy based planner", "energy based daily planner", "energy-based planner", "energy-based daily planner",
    "energy planner", "spoon planner", "time-blindness planner", "brain-dump sheets", "daily planner for adhd",
    "brain dump", "focus sheets", "focus sheets pack", "one-task focus page", "undated planner",
    "undated adhd planner", "energy-based daily planner printable", "adhd printable planner",
    "time blindness planner", "time blindness routine planner", "routine planner for adhd", "visual routine planner",
    "brain dump sheets", "brain dump planner", "focus planner", "focus and brain dump", "adhd daily planner",
    "adhd planner printable", "adhd friendly planner", "neurodivergent planner", "executive function planner",
]
EDITIONS = {
    "energy": {"title": "Energy-Based Daily Planner", "accent": "#2f7d6d",
               "tagline": "Plan your day by your energy, not by the clock"},
    "time_blindness": {"title": "Time-Blindness Routine Planner", "accent": "#3d5a98",
                       "tagline": "Time anchors, simple routines and guess-vs-real time"},
    "focus": {"title": "Focus and Brain-Dump Sheets", "accent": "#7a4c9c",
              "tagline": "Empty your head, pick one task, focus in short blocks"},
}
_EDITION_WORDS = [("time_blindness", r"time[- ]?blind|routine|time anchor|transition"),
                  ("focus", r"brain[- ]?dump|focus sheet|focus block|priorit"),
                  ("energy", r"energy|spoon")]
INK, MUTED, LINE, SOFT, WHITE = "#1f2a30", "#5f6b73", "#bfcbcf", "#eef3f2", "#ffffff"
PAPERS = {"a4": "A4", "letter": "Letter"}
BAND_H, GAP, BOTTOM = 48, 11, 70
DISCLAIMER = "A printable organising tool. It is not medical advice and does not replace professional support."


def edition_of(spec: Dict[str, Any]) -> str:
    raw = str(spec.get("edition") or "").lower().replace("-", "_").replace(" ", "_")
    if raw in EDITIONS:
        return raw
    text = " ".join(str(spec.get(k) or "") for k in ("title", "name", "description", "payload")).lower()
    listing = spec.get("listing") if isinstance(spec.get("listing"), dict) else {}
    text += " " + " ".join(str(listing.get(k) or "") for k in ("title", "description")).lower()
    for edition, pattern in _EDITION_WORDS:
        if re.search(pattern, text):
            return edition
    return "energy"


def normalize_spec(spec: Any) -> Dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")
    edition = edition_of(spec)
    title = " ".join(str(spec.get("title") or spec.get("name") or EDITIONS[edition]["title"]).split())[:80]
    return {"edition": edition, "title": title or EDITIONS[edition]["title"], **{
        k: EDITIONS[edition][k] for k in ("accent", "tagline")}}


# ---- canvases: the PDF writer, and a Pillow twin with the same calls (y up, in points) --------------
class _ImageCanvas:
    def __init__(self, w: float, h: float, scale: float):
        self.w, self.h, self.s = w, h, scale
        self.im = Image.new("RGB", (round(w * scale), round(h * scale)), WHITE)
        self.d = ImageDraw.Draw(self.im)

    def _xy(self, x, y):
        return x * self.s, (self.h - y) * self.s

    def _font(self, size, bold):
        import mockups
        return mockups.font("bold" if bold else "regular", max(4, round(size * self.s)))

    def text(self, x, y, s, size=11, bold=False, color=None, spacing=0):
        self.d.text(self._xy(x, y), s, fill=color or INK, font=self._font(size, bold), anchor="ls")

    def text_center(self, x, y, s, size=11, bold=False, color=None, spacing=0):
        self.d.text(self._xy(x, y), s, fill=color or INK, font=self._font(size, bold), anchor="ms")

    def line(self, x1, y1, x2, y2, gray=0.7, color=None, width=0.6):
        self.d.line([self._xy(x1, y1), self._xy(x2, y2)], fill=color or LINE, width=max(1, round(width * self.s)))

    def rect(self, x, y, w, h, gray=0.7, color=None, width=0.6):
        (a, b), (c, d) = self._xy(x, y + h), self._xy(x + w, y)
        self.d.rectangle([a, b, c, d], outline=color or LINE, width=max(1, round(width * self.s)))

    def fill(self, x, y, w, h, color):
        (a, b), (c, d) = self._xy(x, y + h), self._xy(x + w, y)
        self.d.rectangle([a, b, c, d], fill=color)

    def round_rect(self, x, y, w, h, r, fill=None, stroke=None, width=0.8):
        (a, b), (c, d) = self._xy(x, y + h), self._xy(x + w, y)
        self.d.rounded_rectangle([a, b, c, d], radius=r * self.s, fill=fill, outline=stroke,
                                 width=max(1, round(width * self.s)) if stroke else 0)


class LabelCut(ValueError):
    """A label would not fit its box even at the smallest size: refuse instead of cutting it."""


def _fit(c, s, size, width, bold=False, min_size=6.5, lines=1):
    out, size = fit_text(s, size, width, bold=bold, max_lines=lines, min_size=min_size)
    if len(out) > lines:
        raise LabelCut(f"label does not fit ({width:.0f} pt): {s!r}")
    return out, size


# ---- page parts --------------------------------------------------------------------------------------
def _band(c, title: str, subtitle: str, accent: str) -> float:
    """Header band (accent) + subtitle. Returns the y where content starts."""
    top = c.h - MARGIN
    c.round_rect(MARGIN, top - BAND_H, c.w - 2 * MARGIN, BAND_H, 8, fill=accent)
    lines, size = _fit(c, title, 19, c.w - 2 * MARGIN - 32, bold=True, min_size=11)
    c.text(MARGIN + 16, top - BAND_H / 2 - size * 0.35, lines[0], size, bold=True, color=WHITE)
    y = top - BAND_H - 17
    if subtitle:
        sub, ssize = _fit(c, subtitle, 10, c.w - 2 * MARGIN, min_size=7.5)
        c.text(MARGIN, y, sub[0], ssize, color=MUTED)
    return y - 12


def _heading(c, x, y, w, h, text, accent) -> float:
    c.rect(x, y, w, h, color=LINE, width=0.7)
    c.fill(x, y + h - 20, w, 20, SOFT)
    lines, size = _fit(c, text, 10, w - 14, bold=True, min_size=7)
    c.text(x + 7, y + h - 14, lines[0], size, bold=True, color=accent)
    return y + h - 20      # top of the inner area


def _lines(c, x, y, w, inner_top, step=19):
    n = max(1, int((inner_top - y - 6) // step))
    for i in range(1, n + 1):
        yy = inner_top - i * step
        if yy > y + 4:
            c.line(x + 8, yy, x + w - 8, yy, color=LINE, width=0.5)


def _section(c, x, y, w, h, sec, accent):
    kind = sec["kind"]
    if kind == "fields":
        c.rect(x, y, w, h, color=LINE, width=0.7)
        labels = sec["labels"]
        cw = w / len(labels)
        for i, label in enumerate(labels):
            lx = x + i * cw + 8
            lines, size = _fit(c, label, 9, cw * 0.45, bold=True, min_size=7)
            c.text(lx, y + h / 2 - 3, lines[0], size, bold=True, color=accent)
            from pdf_design import text_width
            start = lx + text_width(lines[0], size, True) + 6
            c.line(start, y + h / 2 - 5, x + (i + 1) * cw - 8, y + h / 2 - 5, color=LINE, width=0.6)
        return
    inner = _heading(c, x, y, w, h, sec["title"], accent)
    if kind == "lines":
        _lines(c, x, y, w, inner, sec.get("step", 19))
    elif kind == "checks":
        items = sec.get("items", [])
        rows = max(len(items), int((inner - y - 4) // 21), 1)
        rh = (inner - y - 4) / rows
        for i in range(rows):
            ry = inner - (i + 1) * rh
            c.rect(x + 9, ry + rh / 2 - 4.5, 9, 9, color=accent, width=0.7)
            if i < len(items):
                lines, size = _fit(c, items[i], 9, w - 40, min_size=6.5)
                c.text(x + 25, ry + rh / 2 - 3, lines[0], size, color=INK)
            else:
                c.line(x + 25, ry + rh / 2 - 4, x + w - 10, ry + rh / 2 - 4, color=LINE, width=0.5)
    elif kind == "choice":
        opts = sec["options"]
        cw = (w - 16) / len(opts)
        cy = y + (inner - y) / 2
        for i, opt in enumerate(opts):
            ox = x + 8 + i * cw
            c.round_rect(ox, cy - 6, 12, 12, 3, stroke=accent, width=0.8)
            lines, size = _fit(c, opt, 9, cw - 22, min_size=6.5)
            c.text(ox + 17, cy - 3, lines[0], size, color=INK)
    elif kind == "table":
        cols = sec["columns"]
        total = sum(wt for _, wt in cols)
        widths = [(w - 2) * wt / total for _, wt in cols]
        heads = [_fit(c, label, 8.5, cw - 8, bold=True, min_size=6.5, lines=2) for (label, _), cw in zip(cols, widths)]
        hh = 28 if any(len(lines) > 1 for lines, _ in heads) else 18
        cx = x + 1
        for (lines, size), cw in zip(heads, widths):
            for j, line in enumerate(lines[:2]):
                c.text(cx + 4, inner - 12 - j * (size + 1.5), line, size, bold=True, color=MUTED)
            cx += cw
        top = inner - hh
        c.line(x, top, x + w, top, color=accent, width=0.8)
        labels = sec.get("rows", [])
        rows = len(labels) if sec.get("exact") else max(1, int((top - y) // sec.get("row", 22)), len(labels))
        rh = (top - y) / rows
        if rh < 13:
            raise LabelCut(f"table '{sec['title']}' rows too short to write in ({rh:.1f} pt)")
        for r in range(1, rows):
            c.line(x, top - r * rh, x + w, top - r * rh, color=LINE, width=0.5)
        cx = x + 1
        for cw in widths[:-1]:
            cx += cw
            c.line(cx, top, cx, y, color=LINE, width=0.5)
        for r, label in enumerate(sec.get("rows", [])):
            cx = x + 1
            for value, cw in zip([label] if isinstance(label, str) else label, widths):
                lines, size = _fit(c, value, 8.5, cw - 8, min_size=6.5)
                c.text(cx + 4, top - r * rh - rh / 2 - 3, lines[0], size, color=INK)
                cx += cw
    elif kind == "grid":
        rows_l, cols_l = sec["rows"], sec["columns"]
        lw = min(78, w * 0.26)
        cw = (w - lw - 8) / len(cols_l)
        for i, label in enumerate(cols_l):
            lines, size = _fit(c, label, 8, cw - 4, bold=True, min_size=6)
            c.text_center(x + lw + i * cw + cw / 2, inner - 13, lines[0], size, bold=True, color=MUTED)
        top = inner - 20
        rh = (top - y - 4) / len(rows_l)
        for r, label in enumerate(rows_l):
            ry = top - (r + 1) * rh
            lines, size = _fit(c, label, 8.5, lw - 10, bold=True, min_size=6.5)
            c.text(x + 7, ry + rh / 2 - 3, lines[0], size, bold=True, color=INK)
            for i in range(len(cols_l)):
                c.rect(x + lw + i * cw + 2, ry + 2, cw - 4, rh - 4, color=LINE, width=0.5)
    elif kind == "dots":
        step = 14
        yy = inner - step
        while yy > y + 6:
            xx = x + 12
            while xx < x + w - 8:
                c.fill(xx, yy, 1.1, 1.1, LINE)
                xx += step
            yy -= step
    elif kind == "text":
        yy = inner - 16
        for para in sec["paragraphs"]:
            for line in wrap_text(para, 10, w - 22):
                c.text(x + 10, yy, line, 10, color=INK)
                yy -= 14
            yy -= 6
        _lines(c, x, y, w, yy + 8, 19)
    elif kind == "matrix":
        labels = sec["quads"]
        mw, mh = (w - 18) / 2, (inner - y - 14) / 2
        for i, label in enumerate(labels):
            qx = x + 6 + (i % 2) * (mw + 6)
            qy = y + 6 + (1 - i // 2) * (mh + 4)
            c.rect(qx, qy, mw, mh, color=accent if i == 0 else LINE, width=0.8)
            lines, size = _fit(c, label, 9, mw - 14, bold=True, min_size=7)
            c.text(qx + 7, qy + mh - 13, lines[0], size, bold=True, color=accent)
            _lines(c, qx, qy, mw, qy + mh - 18, 18)
    else:
        raise ValueError(f"unknown section kind {kind}")


def _layout(c, top, rows, accent):
    """rows: [(weight, [(weight, section), ...]), ...] fill from top down to BOTTOM exactly."""
    total = top - BOTTOM - GAP * (len(rows) - 1)
    weight = sum(r[0] for r in rows)
    y = top
    for rw, cols in rows:
        h = total * rw / weight
        width = c.w - 2 * MARGIN - GAP * (len(cols) - 1)
        cwt = sum(cw for cw, _ in cols)
        x = MARGIN
        for cw, sec in cols:
            sw = width * cw / cwt
            _section(c, x, y - h, sw, h, sec, accent)
            x += sw + GAP
        y -= h + GAP


# ---- the three editions ----------------------------------------------------------------------------
def S(kind, title="", **kw):
    return dict(kind=kind, title=title, **kw)


HOURS = ["6-7", "7-8", "8-9", "9-10", "10-11", "11-12", "12-1", "1-2", "2-3", "3-4", "4-5", "5-6", "6-7 pm",
         "7-8 pm", "8-9 pm", "9-10 pm"]
DATE = S("fields", labels=["Date", "Day"])


def _energy_pages():
    daily = ("Daily Energy Plan", "Check your energy first, then match each task to it.", [
        (0.55, [(1, DATE)]),
        (0.8, [(1, S("choice", "Energy this morning", options=["High", "Medium", "Low"])),
               (1, S("choice", "Energy this afternoon", options=["High", "Medium", "Low"]))]),
        (2.6, [(1, S("checks", "Top 3 for today", items=["", "", ""])),
               (1.25, S("table", "Energy blocks", columns=[("Time", 1), ("Energy H / M / L", 1.1), ("Task", 2.2)]))]),
        (1.9, [(1, S("checks", "Low-energy backup tasks")), (1, S("checks", "Breaks and recharge"))]),
        (2.0, [(1, S("lines", "Brain dump")), (1, S("lines", "One small win today"))])])
    weekly = ("Weekly Energy Review", "Spot your pattern: when were you strongest this week?", [
        (0.55, [(1, S("fields", labels=["Week of", "Review day"]))]),
        (3.0, [(1, S("grid", "Energy by part of day (write H, M or L)", rows=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                     columns=["Morning", "Midday", "Afternoon", "Evening"]))]),
        (2.0, [(1, S("lines", "What gave me energy")), (1, S("lines", "What drained my energy"))]),
        (1.6, [(1, S("checks", "Next week I will try"))])])
    pages = [("How to Use This Planner", "Four steps. Use any page on any day: nothing is dated.", [
        (3.2, [(1, S("text", "How it works", paragraphs=[
            "1. Each morning, tick your energy level on a Daily Energy Plan page.",
            "2. Pick up to three tasks that fit that energy. Big tasks go in high-energy blocks.",
            "3. When energy dips, switch to the low-energy backup list instead of stopping completely.",
            "4. At the end of the week, fill in the Weekly Energy Review to see your own pattern.",
            DISCLAIMER]))]),
        (1.4, [(1, S("checks", "Printing tips", items=["Print at 100% (Actual size)", "Print the daily page as many times as you like",
                                                      "Keep the Task Sorter and Recharge Menu at the front"]))]),
        (1.2, [(1, S("lines", "My usual high-energy time"))])]),
        ("Energy Map", "Mark when your energy is usually high, medium or low.", [
            (0.55, [(1, S("fields", labels=["Started on", "Updated on"]))]),
            (3.2, [(1, S("grid", "My usual energy (H, M or L)", rows=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                         columns=["Early", "Morning", "Midday", "Afternoon", "Evening", "Night"]))]),
            (1.8, [(1, S("lines", "Patterns I notice"))])]),
        ("Task Sorter", "Sort your to-do list by the energy each task needs.", [
            (1, [(1, S("checks", "High-energy tasks")), (1, S("checks", "Medium-energy tasks"))]),
            (1, [(1, S("checks", "Low-energy tasks")), (1, S("checks", "Five-minute tasks"))])])]
    pages += [daily] * 14 + [weekly] * 4
    pages += [("Recharge Menu", "Small things that help you reset. Pick one when your energy is low.", [
        (1, [(1, S("checks", "Five minutes")), (1, S("checks", "Fifteen minutes"))]),
        (1, [(1, S("checks", "Half an hour")), (1, S("checks", "People and places that help"))])]),
        ("Monthly Reflection", "Look back at four weeks of energy notes.", [
            (0.55, [(1, S("fields", labels=["Month", "Year"]))]),
            (1.6, [(1, S("lines", "Best days and why"))]), (1.6, [(1, S("lines", "Hard days and what helped"))]),
            (1.4, [(1, S("checks", "Keep doing")), (1, S("checks", "Change next month"))])]),
        ("Notes", "Extra space for anything else.", [(1, [(1, S("dots", "Notes"))])])]
    return pages


def _time_pages():
    daily = ("Daily Time Anchors", "Write the time anchors first, then fill the hours around them.", [
        (0.55, [(1, DATE)]),
        (1.2, [(1, S("checks", "Now", items=[""])), (1, S("checks", "Next", items=[""])),
               (1, S("checks", "Later", items=[""]))]),
        (4.2, [(1.35, S("table", "Hour by hour", columns=[("Time", 0.9), ("Plan", 2.6)], rows=HOURS, row=18)),
               (1, S("table", "Guess vs real time", columns=[("Task", 1.6), ("Guess", 0.8), ("Real", 0.8)]))]),
        (1.5, [(1, S("checks", "Timers and reminders to set")), (1, S("lines", "Tomorrow's first step"))])])
    weekly = ("Weekly Routine Review", "Which parts of your routines worked this week?", [
        (0.55, [(1, S("fields", labels=["Week of", "Review day"]))]),
        (3.0, [(1, S("grid", "Routine done? (tick)", rows=["Morning routine", "Leaving the house", "Evening routine",
                                                           "Bedtime wind-down", "Weekly reset"],
                     columns=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]))]),
        (1.6, [(1, S("lines", "What made it easier")), (1, S("lines", "What got in the way"))]),
        (1.2, [(1, S("checks", "One change for next week"))])])

    def routine(name):
        return (f"{name} Routine Builder", "List the steps in order and how long each one really takes.", [
            (4.0, [(1, S("table", "Steps", columns=[("#", 0.35), ("Step", 3), ("Minutes", 0.8), ("Done", 0.6)],
                         rows=[str(i) for i in range(1, 13)]))]),
            (1.3, [(1, S("lines", "Start time and finish time")), (1, S("lines", "Things to prepare the night before"))])])
    pages = [("How to Use This Planner", "Undated pages: start on any day and skip days freely.", [
        (3.2, [(1, S("text", "How it works", paragraphs=[
            "1. Build your morning and evening routines once, step by step, with real minutes.",
            "2. Each day, write two or three time anchors (fixed times) on a Daily Time Anchors page.",
            "3. Before a task, write your time guess. After it, write the real time. Over a few weeks the gap "
            "becomes easier to see.",
            "4. Use the Now / Next / Later boxes when switching between tasks.",
            DISCLAIMER]))]),
        (1.4, [(1, S("checks", "Printing tips", items=["Print at 100% (Actual size)", "Keep the routine pages where you can see them",
                                                      "Reprint the daily page as often as you need"]))]),
        (1.2, [(1, S("lines", "My fixed times this week"))])]),
        routine("Morning"), routine("Evening"),
        ("Time Estimate Practice", "Guess first, then time it. No judging, just noticing.", [
            (5, [(1, S("table", "Guess vs real", columns=[("Task", 2.4), ("My guess", 1), ("Real time", 1),
                                                          ("Difference", 1)]))])]),
        ("Transition Checklist", "Small steps that make it easier to switch from one thing to the next.", [
            (1, [(1, S("checks", "Before leaving the house", items=["Keys", "Phone", "Bag", "Water", "Check the time"])),
                 (1, S("checks", "Before starting a task", items=["Clear the desk", "Set a timer", "Write the first step"]))]),
            (1, [(1, S("checks", "Before a break ends")), (1, S("checks", "My own transition steps"))])])]
    pages += [daily] * 14 + [weekly] * 4
    pages += [("Appointments and Reminders", "Write it down the moment you hear it.", [
        (5, [(1, S("table", "Log", columns=[("Date", 0.9), ("Time", 0.7), ("What", 2.2), ("Reminder set", 0.9)]))])]),
        ("Notes", "Extra space for anything else.", [(1, [(1, S("dots", "Notes"))])])]
    return pages


def _focus_pages():
    dump = ("Brain Dump", "Write everything down first. Sort it afterwards.", [
        (0.55, [(1, DATE)]),
        (2.8, [(1, S("dots", "Everything on my mind"))]),
        (2.2, [(1, S("checks", "Do today")), (1, S("checks", "Later this week"))]),
        (1.4, [(1, S("checks", "Ask for help")), (1, S("checks", "Let it go"))])])
    session = ("Focus Session Sheet", "One task, short blocks, a parking lot for distractions.", [
        (0.55, [(1, DATE)]),
        (0.9, [(1, S("lines", "The one task", step=17))]),
        (2.6, [(1.3, S("table", "15-minute focus blocks", columns=[("Block", 0.7), ("What I will do", 2.4), ("Done", 0.6)],
                       rows=[f"Block {i}" for i in range(1, 9)])),
               (1, S("lines", "Distraction parking lot"))]),
        (1.4, [(1, S("checks", "Before I start", items=["Water nearby", "Phone away", "Timer set", "First step written"])),
               (1, S("lines", "What I finished"))])])
    matrix = ("Priority Grid", "Place each task in one box. Start with the top-left box.", [
        (0.55, [(1, DATE)]),
        (5, [(1, S("matrix", "Sort your tasks", quads=["Urgent and important", "Important, not urgent",
                                                      "Urgent, not important", "Neither"]))])])
    pages = [("How to Use These Sheets", "Undated sheets: use them in any order, as often as you like.", [
        (3.2, [(1, S("text", "How it works", paragraphs=[
            "1. Brain Dump: write every thought, task and worry on the page without sorting.",
            "2. Sort the list into Do today, Later this week, Ask for help and Let it go.",
            "3. Focus Session: choose one task, write it at the top, and work in 15-minute blocks. "
            "Park distractions in the side box instead of acting on them.",
            "4. Priority Grid: when everything feels urgent, place each task in one of the four boxes.",
            DISCLAIMER]))]),
        (1.4, [(1, S("checks", "Printing tips", items=["Print at 100% (Actual size)", "Print brain dump pages in batches",
                                                      "Keep a focus sheet next to your work"]))]),
        (1.2, [(1, S("lines", "My best place to focus"))])])]
    pages += [dump] * 8 + [session] * 8 + [matrix] * 4
    pages += [("Ideas Parking Lot", "Good ideas for later, so they do not pull you away now.", [
        (5, [(1, S("table", "Ideas", columns=[("Idea", 3), ("When to look again", 1.2)]))])])] * 2
    pages += [("Weekly Focus Review", "What helped you focus this week?", [
        (0.55, [(1, S("fields", labels=["Week of", "Review day"]))]),
        (2.4, [(1, S("grid", "Focus blocks done", rows=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                     columns=["1", "2", "3", "4", "5", "6", "7", "8"]))]),
        (1.6, [(1, S("lines", "What helped")), (1, S("lines", "What pulled me away"))]),
        (1.2, [(1, S("checks", "Try next week"))])]),
        ("Notes", "Extra space for anything else.", [(1, [(1, S("dots", "Notes"))])])]
    return pages


PAGES = {"energy": _energy_pages, "time_blindness": _time_pages, "focus": _focus_pages}


def page_list(edition: str) -> List[Tuple[str, str, list]]:
    return PAGES[edition]()


def _cover(c, norm, pages):
    accent = norm["accent"]
    top = c.h - MARGIN
    c.round_rect(MARGIN, top - 64, c.w - 2 * MARGIN, 64, 10, fill=accent)
    c.text(MARGIN + 18, top - 40, "UNDATED PRINTABLE PLANNER", 13, bold=True, color=WHITE)
    lines, size = _fit(c, norm["title"], 30, c.w - 2 * MARGIN - 20, bold=True, min_size=18, lines=2)
    y = top - 120
    for line in lines:
        c.text_center(c.w / 2, y, line, size, bold=True, color=INK)
        y -= size * 1.2
    tag, tsize = _fit(c, norm["tagline"], 13, c.w - 2 * MARGIN, min_size=9)
    c.text_center(c.w / 2, y - 6, tag[0], tsize, color=accent)
    groups = []
    for i, (title, _, _) in enumerate(pages, 2):
        if groups and groups[-1][0] == title:
            groups[-1][2] = i
        else:
            groups.append([title, i, i])
    rows = [[t + (f" (x {b - a + 1})" if b > a else ""), f"page {a}" if a == b else f"pages {a}-{b}"]
            for t, a, b in groups]
    sec = S("table", "What is inside", columns=[("Section", 3), ("Pages", 1)], rows=rows, exact=True)
    _section(c, MARGIN, BOTTOM + 34, c.w - 2 * MARGIN, y - 30 - BOTTOM - 34, sec, accent)
    note, nsize = _fit(c, DISCLAIMER, 8.5, c.w - 2 * MARGIN, min_size=6.5)
    c.text(MARGIN, BOTTOM + 14, note[0], nsize, color=MUTED)


def draw_page(c, norm, pages, index):
    """index 0 = cover; 1.. = pages[index-1]."""
    if index == 0:
        _cover(c, norm, pages)
        return
    title, subtitle, rows = pages[index - 1]
    top = _band(c, title, subtitle, norm["accent"])
    _layout(c, top, rows, norm["accent"])


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any], pages) -> int:
    pdf = pdf_design.Pdf(PAPERS[paper], footer=f"{norm['title']} - undated printable")
    for index in range(len(pages) + 1):
        pdf.new_page()
        draw_page(pdf, norm, pages, index)
    pdf.save(str(path), title=norm["title"])
    return len(pages) + 1


def render_page(norm, pages, index, height_px=700) -> "Image.Image":
    w, h = pdf_design.PAGE_SIZES["Letter"]
    canvas = _ImageCanvas(w, h, height_px / h)
    draw_page(canvas, norm, pages, index)
    return canvas.im


def _listing_image(path: Path, norm, pages, picks, caption):
    import mockups
    im = Image.new("RGB", (1200, 800), "#f4f6f5")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1200, 70], fill=norm["accent"])
    face = mockups.font("bold", 26)
    d.text((40, 35), caption, fill=WHITE, font=face, anchor="lm")
    x = 600 - (len(picks) * 520 + (len(picks) - 1) * 40) // 2
    for index in picks:
        page = render_page(norm, pages, index, 690)
        page = page.resize((520, round(page.height * 520 / page.width)))
        im.paste(page, (x, 90))
        d.rectangle([x, 90, x + page.width, 90 + page.height], outline="#c7d0d3", width=2)
        x += 560
    im.save(str(path), format="PNG", optimize=True)


def _readme(norm, pages_total) -> str:
    return "\n".join([
        norm["title"], "=" * min(60, len(norm["title"])), "",
        "WHAT IS INSIDE",
        f"- {FORMAT}_a4.pdf and {FORMAT}_letter.pdf: the same {pages_total} undated pages in A4 and US Letter",
        "- cover.png and three preview images", "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual size).",
        "3. Reprint any page as often as you like: nothing is dated.", "",
        "GOOD TO KNOW",
        f"- {DISCLAIMER}", "",
        "LICENSE", "Personal use only. Do not resell or redistribute the files.", ""])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the ADHD planner previews")
    norm = normalize_spec(spec)
    pages = page_list(norm["edition"])
    out_path = Path(out_dir).resolve()
    temp = Path(tempfile.mkdtemp(prefix="adhd_planner_build_"))
    try:
        names = []
        total = 0
        for paper in PAPERS:
            name = f"{FORMAT}_{paper}.pdf"
            total = _build_pdf(temp / name, paper, norm, pages)
            names.append(name)
        daily = next(i for i, p in enumerate(pages, 1) if "Daily" in p[0] or "Brain Dump" == p[0])
        other = next(i for i, p in enumerate(pages, 1) if i > daily and p[0] != pages[daily - 1][0])
        review = next(i for i, p in enumerate(pages, 1) if "Review" in p[0] or "Grid" in p[0])
        _listing_image(temp / "cover.png", norm, pages, [0, daily], norm["title"])
        _listing_image(temp / "preview_1.png", norm, pages, [daily, 1], "Inside: a full daily page + how to use")
        _listing_image(temp / "preview_2.png", norm, pages, [other, other + 1 if other + 1 <= len(pages) else 2],
                       f"Inside: {pages[other - 1][0]}")
        _listing_image(temp / "preview_3.png", norm, pages, [review, len(pages)], "Inside: review page + notes")
        names += ["cover.png", "preview_1.png", "preview_2.png", "preview_3.png"]
        (temp / "README.txt").write_text(_readme(norm, total), encoding="utf-8")
        (temp / "LICENSE.txt").write_text("Personal use only. Do not resell, share or redistribute these files.\n",
                                          encoding="utf-8")
        names += ["README.txt", "LICENSE.txt"]
        manifest = {"format": FORMAT, "edition": norm["edition"], "title": norm["title"], "pages_per_pdf": total,
                    "undated": True, "paper_sizes": list(PAPERS),
                    "sections": sorted({p[0] for p in pages}), "files": sorted(names + ["manifest.json"])}
        (temp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        names.append("manifest.json")
        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp / name, out_path / name)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return sorted(names)
