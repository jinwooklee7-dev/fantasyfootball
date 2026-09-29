#!/usr/bin/env python3
"""Write human-confirmed Bluesky accounts into social_account as active.

This is the only path by which an account becomes trusted, and it reads from
data/confirmed_accounts.csv -- a file a person edits. Nothing here discovers
accounts, and nothing auto-follows. See CLAUDE.md.

Handles are re-resolved to DIDs on every run rather than taken from the file.
A handle is renameable and squattable; the DID is permanent. If a handle now
points at a DIFFERENT DID than the one already stored, that is exactly the
takeover case the impersonation warning is about, so it is refused and
reported rather than quietly updated.

    uv run scripts/activate_accounts.py
    uv run scripts/activate_accounts.py --dry-run

Not a cron job. Run it when the confirmed list changes.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import csv

import httpx

from ffdash.config import data_dir
from ffdash.db import session, upsert
from ffdash.ingestlog import logged
from ffdash.nflsource import clean_str
from ffdash.timeutil import utc_now_iso

API = "https://public.api.bsky.app/xrpc"
UA = {"User-Agent": "ffdash-activate/1.0"}


def rows_from(path):
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.lstrip().startswith("#")):
            if clean_str(row.get("handle")):
                yield row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = data_dir() / "confirmed_accounts.csv" if args.file is None else args.file

    with session() as conn:
        with logged(conn, "activate_accounts") as run:
            known = {
                r["handle"]: r["did"]
                for r in conn.execute("SELECT handle, did FROM social_account")
            }
            known_by_did = {
                r["did"]: r["handle"]
                for r in conn.execute("SELECT did, handle FROM social_account")
            }

            now = utc_now_iso()
            rows, problems, takeovers = [], [], []

            with httpx.Client(timeout=30, follow_redirects=True) as client:
                for entry in rows_from(path):
                    handle = clean_str(entry["handle"])
                    r = client.get(
                        f"{API}/app.bsky.actor.getProfile",
                        params={"actor": handle},
                        headers=UA,
                    )
                    if r.status_code >= 400:
                        problems.append(f"{handle}: does not resolve")
                        continue
                    profile = r.json()
                    did = profile.get("did")
                    if not did:
                        problems.append(f"{handle}: no DID")
                        continue

                    previous = known.get(handle)
                    if previous and previous != did:
                        # The handle now points somewhere else. Treat as hostile.
                        takeovers.append(f"{handle}: {previous} -> {did}")
                        continue

                    rows.append(
                        {
                            "did": did,
                            "handle": handle,
                            "display_name": clean_str(profile.get("displayName")),
                            "tier": clean_str(entry.get("tier")) or "beat",
                            "team_abbr": clean_str(entry.get("team_abbr")),
                            "domain_verified": 0 if handle.endswith(".bsky.social") else 1,
                            "status": "active",
                            "validated_at": now,
                        }
                    )

            if args.dry_run:
                for row in rows:
                    print(f"  would activate {row['team_abbr'] or '--':4} {row['handle']}")
                run.note = f"dry run; {len(rows)} would be activated"
                return

            run.rows = upsert(conn, "social_account", rows, key=["did"])
            note = [f"{len(rows)} active"]
            if takeovers:
                note.append("HANDLE TAKEOVER, refused: " + "; ".join(takeovers))
            if problems:
                note.append("unresolved: " + "; ".join(problems))
            run.note = "; ".join(note)

        for row in conn.execute(
            "SELECT team_abbr, handle, tier FROM social_account "
            "WHERE status = 'active' ORDER BY team_abbr, handle"
        ):
            print(f"  {row['team_abbr'] or '--':4} {row['tier']:<11} {row['handle']}")


if __name__ == "__main__":
    main()
