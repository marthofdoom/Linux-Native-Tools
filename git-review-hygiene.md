# Procedure: git hygiene for mod work (worktrees, branches, reviewing a diff)

Several agents and people work on the same repos at once. These habits keep
their work apart and make a review catch what it is meant to catch.

## Worktrees for parallel work

One working tree per task. Two tasks in one tree overwrite each other's files,
and two builds in one tree corrupt each other.

```bash
git fetch origin
git worktree add ../<repo>-wt-<topic> -b <type>/<topic> origin/main
cd ../<repo>-wt-<topic>
# ... work, commit, push ...
git push -u origin <type>/<topic>
```

When the branch is merged or dropped:
```bash
git worktree remove ../<repo>-wt-<topic>
git worktree prune
git worktree list                  # check what is still checked out where
```

Start a new branch from `origin/main`, not from whatever the main checkout has
on disk. The main checkout may hold someone else's uncommitted work. Do not
commit, stash or clean files you did not write.

## Branch names

Prefix by the kind of change, then a short topic. Names in use across MFO and
APMF:

| Prefix | For |
|---|---|
| `feat/` | a new behaviour (`feat/mfo-shed-fists-rule`) |
| `fix/` | a bug fix (`fix/mfo-summon-no-fanout`) |
| `hotfix/vX.Y.Z` | a fix cut against an already released line |
| `build/` | build system or dependency changes (`build/commonlib-mit-fork`) |
| `docs/` | documentation only |
| `test/` | a field-test build not meant to merge as is |
| `integration/<date>` | several branches stacked for one deploy |

Work lands on its own branch and is pushed there. Merging to `main`, tagging
and releasing are separate, deliberate steps done after review. An agent never
does them unless the brief says so.

## Review a branch by its own files

Read a branch's version of a file from git, never from a working copy that may
be on another branch:

```bash
git fetch origin
git show origin/<branch>:native/Serialization.cpp | sed -n 560,640p
git diff origin/main...origin/<branch> -- native/Serialization.cpp
git log --oneline origin/main..origin/<branch>
```

The three-dot `main...branch` diff shows only what the branch added since it
left main.

## The diffstat anomaly check (run before any merge, tag or deploy)

A review that only confirms what you expected is not a review. Read the file
list for what you did NOT expect:

```bash
git diff --stat origin/main...origin/<branch>
git diff --name-status origin/main...origin/<branch> | grep -E '^[ADRC]'
```

`--name-status` marks each path `M` (modified), `A` (added), `D` (deleted),
`R` (renamed) or `C` (copied). Every `A`, `D`, `R` or `C` line is a stop until
someone explains why it is there. So is any path outside the task's stated
scope, and any large line count on a file the task never mentioned. Paste that
output verbatim into the review or the merge note.

Why `--name-status` and not only `--stat`: a new `.cpp` in a repo whose CMake
globs its sources (APMF) shows no `CMakeLists.txt` change, so a whole new
module can hide in a diffstat that looks small.

## Comment-only commits

To prove a commit changed only comments, strip `//` comments from each touched
source file on both sides and diff. Identical output means no code moved.

```bash
for f in $(git diff --name-only <base> <commit> -- '*.cpp' '*.h'); do
  diff <(git show <base>:"$f" | sed 's#//.*##') <(git show <commit>:"$f" | sed 's#//.*##') >/dev/null \
    && echo "same  $f" || echo "CODE  $f"
done
```

The `sed` is crude. It also cuts at a `//` inside a string literal, so a
change after that point (a URL, a path) would read as `same`. Check any touched
line that holds a quoted `//` by eye. It does not strip `/* */` block
comments, so a block-comment edit reads as `CODE`. A `CODE` result always
needs a look.

## Before you say something built

CI green counts only for your own commit. See
[native-dll-via-github-actions.md](native-dll-via-github-actions.md#procedure-compile-a-dll-and-prove-the-green-run-is-yours).
