---
name: release
description: Cut a new dbus-systemcalc-py release - verify master is clean and up to date, run tests, bump softwareVersion, commit and tag.
disable-model-invocation: true
---

# Release dbus-systemcalc-py

Run these steps in order from the repository root. If any check or command fails,
stop immediately, report what failed (with output), and make no further changes.

## 1. Pre-flight checks

1. Must be on master: `git rev-parse --abbrev-ref HEAD` prints `master`.
2. No unpulled remote commits: run `git fetch origin`, then
   `git rev-list --count HEAD..origin/master` must print `0`.
3. No uncommitted changes to tracked files:
   `git diff --quiet && git diff --cached --quiet`.
   Untracked files are fine and must be left alone.

## 2. Tests

1. `make test` must pass.
2. `make testinstall` must pass.

## 3. Bump the version

1. Read the current version from the line `softwareVersion = 'X.YYY'` in
   `dbus_systemcalc.py`.
2. The new version is the minor part incremented by one, keeping three-digit
   zero padding (e.g. `2.259` -> `2.260`, `2.099` -> `2.100`). If the user passed
   a version as an argument, use that instead.
3. Change only that line.

## 4. Commit and tag

With NEW being the new version (e.g. `2.260`):

```bash
git commit -m "vNEW" dbus_systemcalc.py
git tag -a NEW -m "vNEW"
```

The commit message and tag message carry the `v` prefix; the tag name does not.
Do not add a Co-Authored-By trailer to the release commit.

## 5. Push (optional)

Show the new version, commit hash and tag, then use AskUserQuestion to ask
whether to push the release to origin now ("Push" / "Don't push").

- If approved, push master and the tag:

  ```bash
  git push origin master NEW
  ```

  Report the result. If the push fails, show the error and do not retry.
- If declined, do not push. Tell the user they can publish later with the
  command above.
