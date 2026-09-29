#!/usr/bin/env python3
"""Find a Bluesky account for each team's beat writers, and prove it is them.

Why this is not a simple handle lookup. Published beat-writer lists carry
Twitter/X handles, and assuming @MikeReiss on X is mikereiss.bsky.social on
Bluesky is exactly how you end up following a typosquatter -- the failure mode
CLAUDE.md calls out, with real money attached. So a candidate is only ever a
candidate here. For each one we:

  1. search Bluesky by the writer's real NAME, and also probe the obvious
     handle spellings derived from the X handle,
  2. score every hit on evidence we can check -- does the bio name the team,
     does it use beat-reporter language, is the handle domain-verified, how
     many followers, is it still posting,
  3. score membership of a curated NFL starter pack separately, because a
     human with a reputation put them on it,
  4. write the lot to CSV for a person to confirm.

Nothing is marked active. Nothing is followed. The output is a worksheet.

    uv run scripts/find_beat_writers.py --writers writers.json -o data/beat_candidates.csv

The writers file is [{"team": "NE", "name": "Mike Reiss", "x": "MikeReiss"}, ...].
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import csv
import json
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

API = "https://public.api.bsky.app/xrpc"
UA = {"User-Agent": "ffdash-beat-finder/1.0"}

# Nicknames are the strong signal that a bio is about the right team.
TEAM_WORDS = {
    "ARI": ["cardinals", "cards", "arizona"],
    "ATL": ["falcons", "atlanta"],
    "BAL": ["ravens", "baltimore"],
    "BUF": ["bills", "buffalo"],
    "CAR": ["panthers", "carolina"],
    "CHI": ["bears", "chicago"],
    "CIN": ["bengals", "cincinnati"],
    "CLE": ["browns", "cleveland"],
    "DAL": ["cowboys", "dallas"],
    "DEN": ["broncos", "denver"],
    "DET": ["lions", "detroit"],
    "GB": ["packers", "green bay"],
    "HOU": ["texans", "houston"],
    "IND": ["colts", "indianapolis", "indy"],
    "JAX": ["jaguars", "jags", "jacksonville"],
    "KC": ["chiefs", "kansas city"],
    "LV": ["raiders", "las vegas"],
    "LAC": ["chargers", "bolts"],
    "LA": ["rams"],
    "MIA": ["dolphins", "miami"],
    "MIN": ["vikings", "minnesota"],
    "NE": ["patriots", "pats", "new england"],
    "NO": ["saints", "new orleans"],
    "NYG": ["giants"],
    "NYJ": ["jets"],
    "PHI": ["eagles", "philadelphia", "philly"],
    "PIT": ["steelers", "pittsburgh"],
    "SF": ["49ers", "niners", "san francisco"],
    "SEA": ["seahawks", "seattle"],
    "TB": ["buccaneers", "bucs", "tampa"],
    "TEN": ["titans", "tennessee", "nashville"],
    "WAS": ["commanders", "washington"],
}

BEAT_WORDS = ("beat", "covers", "covering", "reporter", "writer", "correspondent")

# Phrases that mark an account as NOT the person, however well the name matches.
IMPOSTOR_WORDS = (
    "parody", "fan account", "not affiliated", "unofficial", "commentary account",
    "bot account", "archive of", "mirror", "reposts", "not the real",
)


def get(client: httpx.Client, path: str, **params):
    try:
        r = client.get(f"{API}/{path}", params=params, headers=UA)
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


def candidate_handles(x_handle: str) -> list[str]:
    """Plausible Bluesky spellings of an X handle. Probes, not assumptions."""
    base = (x_handle or "").strip().lstrip("@")
    if not base:
        return []
    plain = base.lower()
    stripped = re.sub(r"[^a-z0-9]", "", plain)
    out = []
    for stem in dict.fromkeys([plain, stripped]):
        out.append(f"{stem}.bsky.social")
    return out


def score_profile(profile: dict, writer: dict, in_pack: bool) -> tuple[int, list[str]]:
    """Evidence that this account is the writer it claims to be."""
    desc = (profile.get("description") or "").lower()
    display = (profile.get("displayName") or "").lower()
    handle = (profile.get("handle") or "").lower()
    text = f"{desc} {display} {handle}"
    reasons = []
    score = 0

    if any(w in text for w in IMPOSTOR_WORDS):
        return -100, ["SELF-DECLARED parody/fan/mirror"]

    # Surname present in the display name is the weakest useful check.
    surname = (writer["name"].split()[-1] or "").lower().strip(".")
    if surname and surname in display:
        score += 2
        reasons.append("name")

    team_hits = [w for w in TEAM_WORDS.get(writer["team"], []) if w in text]
    if team_hits:
        score += 3
        reasons.append("team:" + team_hits[0])

    if any(w in text for w in BEAT_WORDS):
        score += 2
        reasons.append("beat-language")

    # A domain handle means someone proved control of a real domain, which is
    # much harder to fake than registering a *.bsky.social name.
    if not handle.endswith(".bsky.social"):
        score += 3
        reasons.append("domain-verified")

    followers = profile.get("followersCount") or 0
    if followers >= 20000:
        score += 2
        reasons.append(f"{followers // 1000}k followers")
    elif followers >= 3000:
        score += 1
        reasons.append(f"{followers // 1000}k followers")
    elif followers < 300:
        score -= 2
        reasons.append(f"only {followers} followers")

    if in_pack:
        score += 4
        reasons.append("in curated NFL pack")

    return score, reasons


def last_post(client: httpx.Client, did: str) -> tuple[str | None, int]:
    feed = get(client, "app.bsky.feed.getAuthorFeed", actor=did, limit=40)
    if not feed:
        return None, 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    newest = None
    recent = 0
    for item in feed.get("feed", []):
        created = ((item.get("post") or {}).get("record") or {}).get("createdAt")
        if not created:
            continue
        if newest is None or created > newest:
            newest = created
        try:
            when = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= cutoff:
            recent += 1
    return newest, recent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--writers", required=True, help="JSON list of candidates")
    ap.add_argument("--pack", help="JSON list of starter-pack members (optional)")
    ap.add_argument("-o", "--outfile", default="data/beat_candidates.csv")
    args = ap.parse_args()

    writers = json.load(open(args.writers, encoding="utf-8"))
    pack_handles, pack_by_surname = set(), {}
    if args.pack:
        for m in json.load(open(args.pack, encoding="utf-8")):
            handle = (m.get("handle") or "").lower()
            pack_handles.add(handle)
            name = (m.get("name") or "").lower()
            if name:
                pack_by_surname.setdefault(name.split()[-1], []).append(m)

    rows = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for w in writers:
            seen: dict[str, dict] = {}

            # Probe handle spellings derived from the X handle.
            for handle in candidate_handles(w.get("x", "")):
                p = get(client, "app.bsky.actor.getProfile", actor=handle)
                if p:
                    seen[p["did"]] = p
                time.sleep(0.12)

            # Search Bluesky by the writer's actual name.
            found = get(client, "app.bsky.actor.searchActors", q=w["name"], limit=10)
            for p in (found or {}).get("actors", []):
                full = get(client, "app.bsky.actor.getProfile", actor=p["did"])
                if full:
                    seen.setdefault(full["did"], full)
                time.sleep(0.12)

            scored = []
            for did, profile in seen.items():
                in_pack = (profile.get("handle") or "").lower() in pack_handles
                score, reasons = score_profile(profile, w, in_pack)
                scored.append((score, reasons, profile))
            scored.sort(key=lambda s: -s[0])

            if not scored:
                rows.append(
                    {
                        "team": w["team"], "writer": w["name"], "x_handle": w.get("x"),
                        "handle": "", "did": "", "display_name": "", "followers": "",
                        "domain_verified": "", "last_post_at": "", "posts_30d": "",
                        "score": "", "evidence": "", "verdict": "NO BLUESKY ACCOUNT FOUND",
                    }
                )
                print(f"  {w['team']:4} {w['name']:<24} -- none found")
                continue

            score, reasons, profile = scored[0]
            newest, recent = last_post(client, profile["did"])
            handle = profile.get("handle", "")

            if score < 0:
                verdict = "REJECT - self-declared parody/fan"
            elif score >= 8 and recent > 0:
                verdict = "STRONG"
            elif score >= 5 and recent > 0:
                verdict = "LIKELY - eyeball it"
            elif recent == 0:
                verdict = "DORMANT - no posts in 30d"
            else:
                verdict = "WEAK - probably not him"

            rows.append(
                {
                    "team": w["team"], "writer": w["name"], "x_handle": w.get("x"),
                    "handle": handle, "did": profile["did"],
                    "display_name": profile.get("displayName", ""),
                    "followers": profile.get("followersCount", 0),
                    "domain_verified": 0 if handle.endswith(".bsky.social") else 1,
                    "last_post_at": newest or "",
                    "posts_30d": recent,
                    "score": score,
                    "evidence": "; ".join(reasons),
                    "verdict": verdict,
                }
            )
            print(f"  {w['team']:4} {w['name']:<24} {handle:<32} {verdict}")

    with open(args.outfile, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {args.outfile}")
    print("NOTHING is marked active. Confirm each profile yourself before trusting it.")


if __name__ == "__main__":
    main()
