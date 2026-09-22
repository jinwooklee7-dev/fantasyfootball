#!/usr/bin/env python3
"""
Validate candidate Bluesky accounts before they go anywhere near the ingest pipeline.

Resolves each handle to its permanent DID, then reports how recently and how often it
posts, so dead accounts can be cut and impersonators spotted.

Uses the public AppView API. No auth, no API key, no cost.

    uv run scripts/validate_bluesky.py data/bluesky_accounts.csv -o data/validated.csv

Read the output yourself before trusting any of it. A handle resolving successfully
means the account EXISTS, not that it is who it claims to be. Check the follower count,
the post history, and whether the handle is domain-verified.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

API = "https://public.api.bsky.app/xrpc"
UA = {"User-Agent": "ffdash-account-validator/1.0"}


def get_profile(client: httpx.Client, handle: str) -> dict | None:
    r = client.get(f"{API}/app.bsky.actor.getProfile", params={"actor": handle}, headers=UA)
    if r.status_code == 400:
        return None  # unresolvable handle
    r.raise_for_status()
    return r.json()


def get_recent_activity(client: httpx.Client, did: str, days: int = 30) -> tuple[str | None, int]:
    """Return (last_post_iso, posts_in_window). Walks pages until past the window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor, last_post, count = None, None, 0

    for _ in range(10):  # hard page cap
        params = {"actor": did, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            params["cursor"] = cursor
        r = client.get(f"{API}/app.bsky.feed.getAuthorFeed", params=params, headers=UA)
        r.raise_for_status()
        data = r.json()
        feed = data.get("feed", [])
        if not feed:
            break

        for item in feed:
            post = item.get("post", {})
            # Skip reposts: we want what this account actually wrote.
            if item.get("reason"):
                continue
            created = post.get("record", {}).get("createdAt")
            if not created:
                continue
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if last_post is None:
                last_post = created
            if ts >= cutoff:
                count += 1
            else:
                return last_post, count

        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.2)  # be polite

    return last_post, count


def classify(last_post: str | None, per_week: float) -> str:
    if last_post is None:
        return "NO POSTS — drop"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(last_post.replace("Z", "+00:00"))
    if age > timedelta(days=90):
        return "DORMANT — drop"
    if age > timedelta(days=21):
        return "STALE — probably drop"
    if per_week < 1:
        return "LOW VOLUME — judgement call"
    return "ACTIVE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", help="CSV with a 'handle' column")
    ap.add_argument("-o", "--outfile", default="validated.csv")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    with open(args.infile, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("handle", "").strip()
                and not r["handle"].lstrip().startswith("#")]

    out = []
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for row in rows:
            handle = row["handle"].strip().lstrip("@")
            try:
                prof = get_profile(client, handle)
            except httpx.HTTPError as exc:
                print(f"  !! {handle}: {exc}", file=sys.stderr)
                continue

            if prof is None:
                print(f"  ?? {handle}: DOES NOT RESOLVE")
                out.append({**row, "did": "", "verdict": "NOT FOUND"})
                continue

            did = prof["did"]
            last_post, count = get_recent_activity(client, did, args.days)
            per_week = round(count / (args.days / 7), 1)
            verdict = classify(last_post, per_week)
            # A handle that is a real domain means the owner proved control of it.
            domain_verified = not handle.endswith(".bsky.social")

            print(f"  {verdict:24} {handle:38} {per_week:>5}/wk  last={last_post or '-'}")
            out.append({
                **row,
                "did": did,
                "resolved_handle": prof.get("handle", ""),
                "display_name": prof.get("displayName", ""),
                "followers": prof.get("followersCount", 0),
                "domain_verified": int(domain_verified),
                "last_post_at": last_post or "",
                "posts_per_week": per_week,
                "verdict": verdict,
            })
            time.sleep(0.3)

    if out:
        fields = list({k: None for row in out for k in row})
        with open(args.outfile, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(out)
        print(f"\nWrote {len(out)} rows to {args.outfile}")
        print("Review by hand. Set status='active' only for accounts you have confirmed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
