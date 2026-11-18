"""Exercise cards: a short bodyweight set that fits the moment.

PT-style work for shoulders, knees and core, a little cardio, and breathing to wind down. What fits depends
on the time: late at night only breathing (sleep is the better exercise), the harder sets in the day. A run
already logged today (the activity tracker) means no more cardio; anything marked Done today isn't offered
again, and Not now quiets exercise cards for a couple of hours. Exercises axl (or his PT) puts on the
household Exercises list come first: their dose is his, never a guess.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Settings
from .sources import Card, Unavailable

# intensity: calm (breathing, any hour), light (mobility), moderate, hard (daytime only)
LIBRARY: list[dict[str, Any]] = [
    # shoulders
    {"id": "wall-slides", "name": "Wall slides", "area": "Shoulders", "intensity": "light", "minutes": 3,
     "dose": "2 × 10 slow reps",
     "how": "Back, head and forearms against a wall, elbows bent 90°. Slide your arms up as far as they stay on the wall, then back down."},
    {"id": "scap-squeeze", "name": "Shoulder-blade squeezes", "area": "Shoulders", "intensity": "light", "minutes": 2,
     "dose": "2 × 12, hold 3 s each",
     "how": "Sit or stand tall. Pull your shoulder blades back and down, as if into your back pockets, hold, release. No shrugging."},
    {"id": "prone-ytw", "name": "Prone Y-T-W raises", "area": "Shoulders", "intensity": "moderate", "minutes": 5,
     "dose": "2 rounds of 8 Y, 8 T, 8 W",
     "how": "Face down, forehead on a towel. Thumbs up, lift your arms into a Y, then a T, then a W, squeezing the shoulder blades. Small and slow."},
    {"id": "wall-pushups", "name": "Wall push-ups with a plus", "area": "Shoulders", "intensity": "moderate", "minutes": 3,
     "dose": "2 × 12",
     "how": "Hands on a wall at chest height. Push-up, and at the top push a little further so your shoulder blades spread apart."},
    {"id": "doorway-stretch", "name": "Doorway chest stretch", "area": "Shoulders", "intensity": "light", "minutes": 2,
     "dose": "3 × 30 s",
     "how": "Forearms on a door frame, elbows at shoulder height. Step through gently until you feel the front of the shoulders open. No pain."},
    # knees and hips
    {"id": "glute-bridge", "name": "Glute bridges", "area": "Knees & hips", "intensity": "moderate", "minutes": 3,
     "dose": "3 × 12",
     "how": "On your back, knees bent, feet flat. Squeeze your glutes and lift your hips until knees, hips and shoulders line up. Lower slowly."},
    {"id": "slr", "name": "Straight-leg raises", "area": "Knees & hips", "intensity": "light", "minutes": 3,
     "dose": "2 × 10 each leg",
     "how": "On your back, one knee bent. Tighten the other thigh, keep that knee straight and lift the leg to the bent knee's height. Lower slowly."},
    {"id": "wall-sit", "name": "Wall sit", "area": "Knees & hips", "intensity": "hard", "minutes": 3,
     "dose": "3 × 30 s",
     "how": "Back against a wall, slide down until your knees are at about 60° (not 90° if it bothers them). Knees over ankles."},
    {"id": "clamshells", "name": "Clamshells", "area": "Knees & hips", "intensity": "light", "minutes": 3,
     "dose": "2 × 15 each side",
     "how": "On your side, knees bent, heels together. Lift the top knee without rolling your hips back."},
    {"id": "step-ups", "name": "Step-ups", "area": "Knees & hips", "intensity": "hard", "minutes": 4,
     "dose": "2 × 10 each leg",
     "how": "On a stair or sturdy step, push through the whole foot of the stepping leg; knee tracks over your toes. Control the way down."},
    # core
    {"id": "dead-bug", "name": "Dead bugs", "area": "Core", "intensity": "moderate", "minutes": 3,
     "dose": "3 × 8 each side",
     "how": "On your back, arms up, knees over hips. Lower the opposite arm and leg slowly while your low back stays down. Breathe out as you go."},
    {"id": "bird-dog", "name": "Bird dogs", "area": "Core", "intensity": "light", "minutes": 3,
     "dose": "2 × 8 each side, hold 3 s",
     "how": "On hands and knees. Reach one arm forward and the opposite leg back, level, without tipping. Back to center, switch."},
    {"id": "plank", "name": "Forearm plank", "area": "Core", "intensity": "hard", "minutes": 3,
     "dose": "3 × 30 s",
     "how": "Forearms and toes (or knees), body in one line. Squeeze glutes, ribs down, breathe. Stop before your back sags."},
    {"id": "side-plank", "name": "Side plank", "area": "Core", "intensity": "hard", "minutes": 3,
     "dose": "2 × 20 s each side",
     "how": "On one forearm, elbow under shoulder, knees bent or legs straight. Lift your hips into a straight line."},
    # cardio
    {"id": "brisk-walk", "name": "Brisk walk", "area": "Cardio", "intensity": "moderate", "minutes": 15, "cardio": True,
     "dose": "15 minutes",
     "how": "Out the door at a pace where talking is possible but singing isn't."},
    {"id": "bw-circuit", "name": "Bodyweight circuit", "area": "Cardio", "intensity": "hard", "minutes": 10, "cardio": True,
     "dose": "3 rounds: 10 squats, 10 wall push-ups, 20 s march in place",
     "how": "Keep moving between exercises, rest a minute between rounds. Squat only as deep as your knees like."},
    # breathing / wind-down
    {"id": "box-breathing", "name": "Box breathing", "area": "Breathing", "intensity": "calm", "minutes": 4,
     "dose": "4 minutes",
     "how": "In for 4, hold for 4, out for 4, hold for 4. Let your shoulders drop on each breath out."},
    {"id": "478", "name": "4-7-8 breathing", "area": "Breathing", "intensity": "calm", "minutes": 3,
     "dose": "4 rounds",
     "how": "Breathe in through your nose for 4, hold for 7, out through your mouth for 8. A good one in bed."},
    {"id": "sigh", "name": "Physiological sighs", "area": "Breathing", "intensity": "calm", "minutes": 2,
     "dose": "5 slow rounds",
     "how": "Two breaths in through the nose (a full one, then a short top-up), one long slow breath out through the mouth."},
    {"id": "legs-up", "name": "Legs up the wall", "area": "Breathing", "intensity": "calm", "minutes": 5,
     "dose": "5 minutes",
     "how": "Lie down with your legs resting up a wall, arms loose. Slow breaths out, longer than the breaths in."},
]
HARD = {"hard"}


def slot(now: datetime) -> tuple[str, set[str]]:
    """The part of the day and the intensities that fit it."""
    h = now.hour + now.minute / 60
    if h >= 21.5 or h < 5:
        return "late", {"calm"}
    if h < 9:
        return "morning", {"calm", "light", "moderate"}
    if h < 19:
        return "day", {"light", "moderate", "hard"}
    return "evening", {"calm", "light", "moderate"}


def from_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The household Exercises list's open items: name from the text, dose and cues from the notes."""
    out = []
    for i in items:
        text = " ".join(str(i.get("text") or "").split())
        if not text:
            continue
        out.append({"id": "list-" + hashlib.sha1(text.encode()).hexdigest()[:10], "name": text[:120], "area": "Your PT",
                    "intensity": "light", "minutes": 5, "dose": "", "how": str(i.get("notes") or "").strip()[:600],
                    "yours": True})
    return out


def choose(rng: random.Random, now: datetime, done_today: set[str], ran_today: bool,
           mine: list[dict[str, Any]]) -> dict[str, Any]:
    part, fits = slot(now)
    pool = [e for e in mine + LIBRARY
            if e["id"] not in done_today
            and (e["intensity"] in fits or (e.get("yours") and part != "late"))
            and not (ran_today and e.get("cardio"))]
    if not pool:
        raise Unavailable("nothing fits right now")
    areas_done = {e["area"] for e in mine + LIBRARY if e["id"] in done_today}
    weights = [(3 if e.get("yours") else 1) * (0.4 if e["area"] in areas_done else 1) for e in pool]
    return rng.choices(pool, weights=weights)[0]


def card_for(e: dict[str, Any], now: datetime, ran_today: bool) -> Card:
    part, _ = slot(now)
    notes = []
    if part == "late":
        notes.append("It's late: this one helps you sleep. Sleep is the best training tonight.")
    if ran_today and e["area"] == "Cardio":
        notes.append("You ran today.")
    blurb = " ".join([e["how"], "Stop if anything hurts (beyond normal effort).", *notes]).strip()
    return Card(
        kind="exercise",
        label=f"Exercise · {e['area']} · {e['minutes']} min",
        title=e["name"],
        subtitle=e["dose"],
        blurb=blurb,
        url="https://www.youtube.com/results?" + urlencode({"search_query": f"how to do {e['name']} physical therapy"}),
        open_label="Show me how",
        exercise={"id": e["id"], "name": e["name"], "area": e["area"]},
        ref=f"exercise:{e['id']}:{now.date().isoformat()}",
    )


async def ran_today(client: httpx.AsyncClient, s: Settings, now: datetime) -> bool:
    """A run (or other cardio) already logged today in the activity tracker. False when it isn't configured."""
    if not (s.activity_url and s.activity_token):
        return False
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    r = await client.get(f"{s.activity_url}/api/activities", params={"since": start.isoformat(), "limit": 20},
                         headers={"Authorization": f"Bearer {s.activity_token}"}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    return any(str(a.get("type") or a.get("activity_type") or "").lower() in ("running", "run", "cycling", "paddling")
               for a in rows or [] if isinstance(a, dict))
