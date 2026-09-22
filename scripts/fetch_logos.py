#!/usr/bin/env python3
"""Cache team logos locally, once.

The page must render when an external service is down -- Sunday morning is
exactly when you would notice a dead CDN -- so logos are downloaded rather than
hotlinked. 32 small PNGs, fetched once, served from web/static/logos/.

Any team whose file is missing falls back to a coloured badge with its
abbreviation, so a failed download degrades rather than breaking the layout.

    uv run scripts/fetch_logos.py
    uv run scripts/fetch_logos.py --force   # re-download existing files

Not a cron job. Run it after init_db, and again only if a team rebrands.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse

import httpx

from ffdash.config import REPO_ROOT
from ffdash.db import session
from ffdash.ingestlog import logged

LOGO_DIR = REPO_ROOT / "web" / "static" / "logos"

# Small enough that anything larger is an error page, not a PNG.
MIN_BYTES = 200


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    args = ap.parse_args()

    LOGO_DIR.mkdir(parents=True, exist_ok=True)

    with session() as conn:
        with logged(conn, "logos") as run:
            teams = conn.execute(
                "SELECT team_abbr, logo_url FROM team "
                "WHERE logo_url IS NOT NULL ORDER BY team_abbr"
            ).fetchall()

            downloaded = skipped = failed = 0
            problems = []

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                for row in teams:
                    abbr = row["team_abbr"]
                    target = LOGO_DIR / f"{abbr}.png"

                    if target.exists() and not args.force:
                        skipped += 1
                        continue

                    try:
                        response = client.get(row["logo_url"])
                        response.raise_for_status()
                        body = response.content
                        if len(body) < MIN_BYTES:
                            raise ValueError(f"suspiciously small ({len(body)} bytes)")
                        target.write_bytes(body)
                        downloaded += 1
                    except Exception as exc:
                        failed += 1
                        problems.append(f"{abbr}: {type(exc).__name__}")
                        print(f"  {abbr}: {exc}")

            run.rows = downloaded
            run.note = f"{downloaded} downloaded, {skipped} already cached, {failed} failed"
            if problems:
                run.note += "; " + ", ".join(problems)

    print(f"\nlogos in {LOGO_DIR}")
    print("Teams without a cached file fall back to a coloured initials badge.")


if __name__ == "__main__":
    main()
