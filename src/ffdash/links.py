"""URL building, shared by the live app and the static renderer.

The two need different links for the same page. Served by FastAPI a game is
`/game/2026_03_KC_MIA`; built as files it is `/ffdash/game/2026_03_KC_MIA.html`.
Templates call `url(...)` and stay identical either way.

Static links are root-relative with a configurable base, rather than relative
paths like `../game/x.html`. Relative links have to know how deep the current
page sits, which is exactly the sort of thing that breaks silently on one page
out of three hundred.
"""

from __future__ import annotations

from typing import Callable


def live_url() -> Callable[..., str]:
    """Links for the running FastAPI app."""

    def url(kind: str, **kw) -> str:
        if kind == "index":
            return "/"
        if kind == "week":
            return "/?season={}&week={}".format(kw["season"], kw["week"])
        if kind == "game":
            return "/game/{}".format(kw["game_id"])
        if kind == "player":
            return "/player/{}".format(kw["player_id"])
        if kind == "static":
            return "/static/{}".format(kw["path"])
        raise ValueError("unknown link kind: {}".format(kind))

    return url


def normalise_base(base: str) -> str:
    """Validate and normalise the site's base path to '/' or '/segment/'.

    A wrong base does not fail loudly -- it produces a complete site in which
    every single link is broken, which is far worse than an error. Two ways it
    happens in practice: passing a full URL instead of a path, and Git Bash
    silently rewriting a leading-slash argument into a Windows path
    (FFDASH_BASE_URL=/ffdash/ arrives as C:/Program Files/Git/ffdash/).
    """
    text = (base or "/").strip()
    if not text:
        return "/"
    if "://" in text or text.lower().startswith("www."):
        raise ValueError(
            "base path must be a path, not a URL: {!r}. Use '/' or '/<repo>/'.".format(
                base
            )
        )
    if "\\" in text or (len(text) > 1 and text[1] == ":"):
        raise ValueError(
            "base path looks like a filesystem path: {!r}. This usually means a "
            "POSIX shell rewrote it -- prefix the command with MSYS_NO_PATHCONV=1 "
            "or pass --base from PowerShell.".format(base)
        )
    trimmed = text.strip("/")
    return "/{}/".format(trimmed) if trimmed else "/"


def static_url(base: str = "/") -> Callable[..., str]:
    """Links for the generated site.

    `base` is the path the site is served from: "/" for a user site or a custom
    domain, "/<repo>/" for a GitHub Pages project site.
    """
    base = normalise_base(base)

    def url(kind: str, **kw) -> str:
        if kind == "index":
            return base
        if kind == "week":
            return "{}week/{}-{}.html".format(base, kw["season"], kw["week"])
        if kind == "game":
            return "{}game/{}.html".format(base, kw["game_id"])
        if kind == "player":
            return "{}player/{}.html".format(base, kw["player_id"])
        if kind == "static":
            return "{}static/{}".format(base, kw["path"])
        raise ValueError("unknown link kind: {}".format(kind))

    return url


def output_path(kind: str, **kw) -> str:
    """Where the renderer writes each page, relative to the output directory."""
    if kind == "index":
        return "index.html"
    if kind == "week":
        return "week/{}-{}.html".format(kw["season"], kw["week"])
    if kind == "game":
        return "game/{}.html".format(kw["game_id"])
    if kind == "player":
        return "player/{}.html".format(kw["player_id"])
    raise ValueError("unknown page kind: {}".format(kind))
