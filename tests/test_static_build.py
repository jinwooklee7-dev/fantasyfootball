"""Checks on the generated site.

Skips cleanly when dist/ has not been built. Run the renderer first:

    uv run scripts/render_static.py && uv run pytest
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

DIST = Path(__file__).resolve().parents[1] / "dist"
ATTR = re.compile(r'(?:href|src)="([^"]+)"')


@pytest.fixture(scope="module")
def pages():
    if not DIST.exists():
        pytest.skip("dist/ not built; run scripts/render_static.py")
    found = list(DIST.rglob("*.html"))
    if not found:
        pytest.skip("dist/ is empty")
    return found


@pytest.fixture(scope="module")
def base(pages):
    """The base path this build was rendered with, read back off the page.

    A GitHub Pages project site is served from /<repo>/, so links carry that
    prefix while the files sit at the root of dist/. Inferring it from the
    stylesheet link keeps the link check honest for both layouts, and doubles
    as a check that the prefix was applied at all.
    """
    index = (DIST / "index.html").read_text(encoding="utf-8")
    match = re.search(r'href="([^"]*?)static/app\.css"', index)
    assert match, "index.html does not link the stylesheet"
    return match.group(1) or "/"


def test_no_broken_internal_links(pages, base):
    """The failure this catches is a link that 404s on one page in three
    hundred -- invisible until someone clicks it."""
    broken = Counter()
    outside = Counter()
    checked = 0
    for page in pages:
        for raw in ATTR.findall(page.read_text(encoding="utf-8")):
            if raw.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            target = raw.split("#")[0].split("?")[0]
            if not target:
                continue
            if not target.startswith(base):
                # A root-relative link that skips the base would 404 once the
                # site is served from /<repo>/, which is the whole failure mode.
                outside[raw] += 1
                continue
            checked += 1
            relative = target[len(base):]
            candidate = DIST / relative if relative else DIST / "index.html"
            if candidate.is_dir():
                candidate = candidate / "index.html"
            if not candidate.exists():
                broken[raw] += 1
    assert not outside, "links missing the base {!r}: {}".format(
        base, outside.most_common(5)
    )
    assert not broken, "broken links: {}".format(broken.most_common(10))
    # A test that silently checks nothing is worse than no test: if the pages
    # ever render without links, this should fail rather than pass quietly.
    assert checked > len(pages), "only {} links across {} pages".format(
        checked, len(pages)
    )


def test_landing_page_exists(pages):
    assert (DIST / "index.html").exists()


def test_attribution_on_every_page(pages):
    """CC-BY requires it on anything that shows the data. A page generated
    without it is a licence breach sitting on a public URL."""
    missing = [
        p.relative_to(DIST).as_posix()
        for p in pages
        if "nflverse" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, "pages with no attribution: {}".format(missing[:5])


def test_no_absolute_localhost_links(pages):
    """A hard-coded dev URL would work locally and break for everyone else."""
    offenders = [
        p.relative_to(DIST).as_posix()
        for p in pages
        if "127.0.0.1" in p.read_text(encoding="utf-8")
        or "localhost:" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, "localhost links: {}".format(offenders[:5])
