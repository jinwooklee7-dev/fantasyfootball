#!/usr/bin/env python3
"""Render the whole dashboard to plain HTML files.

The site is read-only, so it does not need a server at all. This walks every
week and every game, writes them to dist/, and copies the stylesheet and logos
alongside. Point Cloudflare Pages or GitHub Pages at dist/ and it is live.

Player pages are rendered only for players actually linked from a game page --
building all 24,000 would be absurd, and guessing would leave dead links.

    uv run scripts/render_static.py
    uv run scripts/render_static.py --base /ffdash/     # GitHub Pages project site
    uv run scripts/render_static.py --season 2026 --out dist

The base path matters: a GitHub Pages project site is served from
https://<user>.github.io/<repo>/, so every link needs that prefix. A custom
domain or Cloudflare Pages uses the default "/".
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import os
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ffdash import views
from ffdash.config import REPO_ROOT, current_season, public_mode
from ffdash.db import session
from ffdash.links import normalise_base, output_path, static_url
from ffdash.templating import configure

TEMPLATES = REPO_ROOT / "web" / "templates"
STATIC = REPO_ROOT / "web" / "static"

# Asked of crawlers, not enforced. A GitHub Pages site is public whether or not
# it is indexed; this only keeps it out of search results.
ROBOTS_NOINDEX = "User-agent: *\nDisallow: /\n"
ROBOTS_PUBLIC = "User-agent: *\nAllow: /\n"


def build_env(base: str) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    configure(env, static_url(base))
    return env


def write(out_dir: Path, rel: str, html: str) -> None:
    target = out_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    ap.add_argument(
        "--base",
        default=os.getenv("FFDASH_BASE_URL", "/"),
        help="path the site is served from, e.g. /ffdash/ (default: /)",
    )
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--weeks", type=int, default=6, help="trailing window")
    ap.add_argument(
        "--noindex",
        action="store_true",
        default=True,
        help="ask crawlers to stay away (default on)",
    )
    ap.add_argument("--allow-index", dest="noindex", action="store_false")
    args = ap.parse_args()

    out_dir = REPO_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)

    # Fail before writing 900 pages whose links all point somewhere useless.
    try:
        base = normalise_base(args.base)
    except ValueError as exc:
        print("error: {}".format(exc))
        sys.exit(2)

    env = build_env(base)

    # A clean build: a game that leaves the schedule should not linger as a
    # stale page that nothing links to any more.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    with session() as conn:
        season = args.season or current_season()
        weeks = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT week FROM game WHERE season = ? ORDER BY week",
                (season,),
            )
        ]
        if not weeks:
            print(f"no games stored for season {season}; run refresh_all.py first")
            sys.exit(1)

        default_week = views.current_week(conn, season)
        pages = 0
        player_ids: set[int] = set()

        # --- week pages -----------------------------------------------------
        week_tpl = env.get_template("week.html")
        for week in weeks:
            context = views.week_context(conn, season, week)
            html = week_tpl.render(**context)
            write(out_dir, output_path("week", season=season, week=week), html)
            pages += 1
            if week == default_week:
                # The landing page is whichever week is live right now.
                write(out_dir, "index.html", html)
                pages += 1

        # --- game pages -----------------------------------------------------
        game_tpl = env.get_template("game.html")
        game_ids = [
            r[0]
            for r in conn.execute(
                "SELECT game_id FROM game WHERE season = ? ORDER BY week, kickoff_utc",
                (season,),
            )
        ]
        for game_id in game_ids:
            context = views.game_context(conn, game_id, args.weeks)
            if context is None:
                continue
            html = game_tpl.render(**context)
            write(out_dir, output_path("game", game_id=game_id), html)
            player_ids |= views.players_on_game_page(context)
            pages += 1

        # --- player pages, only the ones something links to ------------------
        player_tpl = env.get_template("player.html")
        for player_id in sorted(player_ids):
            context = views.player_context(conn, player_id, season, weeks=8)
            if context is None:
                continue
            html = player_tpl.render(**context)
            write(out_dir, output_path("player", player_id=player_id), html)
            pages += 1

    # --- assets -------------------------------------------------------------
    shutil.copytree(STATIC, out_dir / "static", dirs_exist_ok=True)
    if public_mode():
        # Logos are suppressed in public mode; do not ship the files either.
        shutil.rmtree(out_dir / "static" / "logos", ignore_errors=True)

    (out_dir / "robots.txt").write_text(
        ROBOTS_NOINDEX if args.noindex else ROBOTS_PUBLIC, encoding="utf-8"
    )
    # Stops GitHub Pages running the output through Jekyll.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print(f"\n{pages} pages -> {out_dir}")
    print(f"  {len(game_ids)} games, {len(player_ids)} players, {len(weeks)} weeks")
    print(f"  base path {base!r}, {size / 1_048_576:.1f} MB total")
    print(f"  robots.txt: {'noindex' if args.noindex else 'indexable'}")
    if not public_mode():
        print("  NOTE: team logos included (club trademarks). Set FFDASH_PUBLIC=1")
        print("        to build with the coloured initials badges instead.")


if __name__ == "__main__":
    main()
