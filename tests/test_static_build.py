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


def test_no_broken_internal_links(pages):
    """The failure this catches is a link that 404s on one page in three
    hundred -- invisible until someone clicks it."""
    broken = Counter()
    for page in pages:
        for raw in ATTR.findall(page.read_text(encoding="utf-8")):
            if raw.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            target = raw.split("#")[0].split("?")[0]
            if not target:
                continue
            candidate = DIST / target.lstrip("/")
            if candidate.is_dir() or target.rstrip("/") == "":
                candidate = candidate / "index.html"
            if not candidate.exists():
                broken[raw] += 1
    assert not broken, "broken links: {}".format(broken.most_common(10))


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
