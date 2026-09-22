"""The game page. FastAPI + Jinja + HTMX, no SPA and no build step.

One route matters: /game/{game_id}. Everything else exists to get you there.

Page content is built in ffdash.views, which the static renderer also uses, so
the served site and the generated one cannot drift apart.

Every panel renders from whatever is in the database and stamps itself with how
old that is. A failed ingest shows a stale timestamp; it never blanks the page.
See CLAUDE.md.

    uv run uvicorn web.app:app --reload
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from ffdash import queries as q  # noqa: E402
from ffdash import views  # noqa: E402
from ffdash.config import current_season  # noqa: E402
from ffdash.db import connect  # noqa: E402
from ffdash.links import live_url  # noqa: E402
from ffdash.templating import configure  # noqa: E402
from ffdash.timeutil import humanise_age  # noqa: E402

app = FastAPI(title="ffdash")
app.mount("/static", StaticFiles(directory=REPO_ROOT / "web" / "static"), name="static")

templates = Jinja2Templates(directory=str(REPO_ROOT / "web" / "templates"))
configure(templates.env, live_url())


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    season: int | None = None,
    week: int | None = None,
    scoring: str = "ppr",
):
    conn = connect()
    try:
        season = season or current_season()
        week = week or views.current_week(conn, season)
        return templates.TemplateResponse(
            request, "week.html", views.week_context(conn, season, week, scoring)
        )
    finally:
        conn.close()


@app.get("/game/{game_id}", response_class=HTMLResponse)
def game_page(request: Request, game_id: str, weeks: int = 6, scoring: str = "ppr"):
    """The page. Panels in priority order: line, weather, injury, usage, matchup."""
    conn = connect()
    try:
        context = views.game_context(conn, game_id, weeks, scoring)
        if context is None:
            return templates.TemplateResponse(
                request,
                "missing.html",
                {"game_id": game_id, **views.shell(conn)},
                status_code=404,
            )
        return templates.TemplateResponse(request, "game.html", context)
    finally:
        conn.close()


@app.get("/player/{player_id}", response_class=HTMLResponse)
def player_page(request: Request, player_id: int, weeks: int = 8, scoring: str = "ppr"):
    """Full trailing log for one player. Reached from the usage block."""
    conn = connect()
    try:
        context = views.player_context(conn, player_id, current_season(), weeks, scoring)
        if context is None:
            return RedirectResponse("/")
        return templates.TemplateResponse(request, "player.html", context)
    finally:
        conn.close()


@app.get("/health")
def health():
    """Ingest freshness as JSON, for eyeballing whether cron is alive."""
    conn = connect()
    try:
        fresh = views.mark_stale(q.freshness(conn))
        return {
            "ok": True,
            "season": current_season(),
            "stale": sorted(k for k, v in fresh.items() if v["stale"]),
            "sources": {
                k: {
                    "last_success": v["finished_at"],
                    "age": humanise_age(v["finished_at"]),
                    "stale": v["stale"],
                }
                for k, v in fresh.items()
            },
        }
    finally:
        conn.close()
