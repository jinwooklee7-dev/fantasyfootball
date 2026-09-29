# Git, for this project

Written for someone who has not used git before. It covers the handful of
commands you will actually use on `fantasyfootball`, and skips the rest.

Run everything from the project folder:

```bash
cd "C:\Users\jinwo\Desktop\Claude\fantasy-football"
```

## What git is doing

Git keeps **save points** for the project. Each one is a *commit*: a snapshot of
every file, plus a note saying what changed and why. Nothing is ever lost once
it is committed, which is what makes it safe to change things.

There are three places a change can be:

```
  your files          staging            commits          GitHub
  (the folder)   ->   (git add)   ->   (git commit)  ->  (git push)
   "working"         "staged"          "saved here"     "backed up there"
```

Staging exists so you can commit *some* changes and not others. Early on you
will almost always stage everything, so it will feel like a formality. That is
fine.

**Committing is local.** Nothing reaches GitHub until you `push`. You can commit
on a plane.

## The four commands

### 1. What changed?

```bash
git status
```

Run this constantly. It tells you what is modified, what is staged, and whether
you are ahead of GitHub. When you are unsure what state you are in, this is the
answer. It cannot break anything.

### 2. Stage the changes

```bash
git add -A
```

`-A` means "everything, including new and deleted files". To stage one file
instead: `git add README.md`.

### 3. Save the snapshot

```bash
git commit -m "Fix the wind threshold"
```

The message is for you in six months. Say *why*, not *what* -- the diff already
shows what. "Fix crash" is weak; "Handle a missing kickoff time" is useful.

For a longer message, leave off `-m` and git opens an editor. If that happens
before you have configured one and you get stuck: press `Esc`, then type `:wq`
and press Enter.

### 4. Send it to GitHub

```bash
git push
```

That is it. Refresh <https://github.com/jinwooklee7-dev/fantasyfootball> and
your changes are there.

## The everyday loop

```bash
git status                          # see what you changed
git add -A                          # stage all of it
git commit -m "Add Cardinals beat writer"
git push                            # back it up
```

Do that whenever you finish something that works. Small, frequent commits are
much easier to undo than one enormous one.

## Looking at history

```bash
git log --oneline              # one line per commit, newest first
git log --oneline -5           # just the last five
git show fce7381               # everything that changed in one commit
git diff                       # what you changed but have not staged
```

Press `q` to get out of any of those when they fill the screen.

## Undoing things

Ordered from safest to most dangerous.

**Discard changes to one file you have not committed:**
```bash
git restore web/static/app.css
```
That file goes back to its last committed state. The change is gone for good --
it was never saved, so git cannot recover it.

**Unstage something you added by mistake** (keeps your edits):
```bash
git restore --staged data/big_file.csv
```

**Undo a commit you already pushed** -- the safe way:
```bash
git revert fce7381
```
This makes a *new* commit that reverses the old one. History stays honest and
nothing disappears. This is almost always what you want.

**Fix the message on the very last commit** (only if you have not pushed it):
```bash
git commit --amend -m "A better message"
```

## Three commands to avoid for now

These destroy work in ways that are hard or impossible to recover:

- `git reset --hard` -- throws away all uncommitted changes with no warning
- `git push --force` -- overwrites what is on GitHub, including other people's work
- `git clean -fd` -- deletes untracked files off your disk

There is no reason to reach for any of them yet. If you ever think you need
one, that is the moment to stop and ask.

## What is deliberately not committed

`.gitignore` lists files git ignores. In this project:

- `db/ffdash.db` -- the database, ~80 MB, rebuilt from the ingests
- `dist/` -- the generated site, rebuilt by `render_static.py`
- `.venv/` -- installed packages, ~217 MB, rebuilt by `uv sync`
- `.env` -- **secrets. Never commit this.** `.env.example` shows what goes in it
- `web/static/logos/` -- NFL club trademarks, re-fetched by `fetch_logos.py`

The rule: commit the things you *wrote*, not the things a command can
*regenerate*. It keeps the repo small and the history readable.

## When something goes wrong

**"Everything is broken and I want to start over from the last save."**
```bash
git status                  # look first -- what would you lose?
git stash                   # tucks your changes away safely
```
`git stash` is the gentle version of `reset --hard`: your changes go into a
holding area rather than the bin. Get them back with `git stash pop`.

**"I committed something I should not have."**
If you have not pushed, `git reset --soft HEAD~1` undoes the commit and keeps
the changes staged. If you *have* pushed -- and it was a secret -- rotate the
secret first. Removing it from GitHub does not un-leak it.

**"git push says rejected."**
Someone (or a GitHub Action) changed the remote since you last pulled:
```bash
git pull --rebase
git push
```

## Terms you will see

| Term | Means |
|---|---|
| repository / repo | the project folder that git is tracking |
| commit | one save point |
| `main` | the default branch -- the line of history you are on |
| remote / `origin` | GitHub's copy |
| push / pull | send to GitHub / fetch from GitHub |
| staged | marked to go into the next commit |
| branch | a parallel line of work; you do not need one yet |
| `HEAD` | the commit you are currently sitting on |

## Later, not now

Branches, pull requests and merges matter when several people work on the same
code, or when you want to try something risky without disturbing `main`. Working
directly on `main` is completely reasonable for a personal project. Come back to
branches when you feel the need, not before.
