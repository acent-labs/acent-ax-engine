# Upstream Sync Workflow

This fork tracks [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
All ACENT customization lives under namespaced paths (`skills/acent/`, `acent/`)
so that `git merge upstream/main` is conflict-free under normal upstream activity.

## Remotes (set automatically by `gh repo clone`)

```
origin    https://github.com/acent-labs/acent-ax-engine.git
upstream  https://github.com/NousResearch/hermes-agent.git
```

If `upstream` is missing on a fresh clone:

```bash
git remote add upstream https://github.com/NousResearch/hermes-agent.git
```

## Routine sync (recommended weekly)

```bash
# 1. Fetch upstream
git fetch upstream

# 2. Make sure local main is clean and on the latest origin
git checkout main
git pull --ff-only origin main

# 3. Merge upstream (no rebase — preserve clear "merge upstream X" commits)
git merge upstream/main --no-ff -m "chore: merge upstream/main"

# 4. Verify our overlay still loads
hermes --version
ls skills/acent/             # ACENT skills should still be here
ls acent/                    # ACENT overlay should still be here

# 5. Push
git push origin main
```

If `git merge` reports conflicts, it almost certainly means upstream
introduced a file path that collides with our overlay. Investigate before
resolving — we'd rather rename our path than carry a perpetual conflict.

## Major version upgrades (e.g. v0.13 → v0.14)

Same workflow as above, but additionally:

1. Read upstream's `RELEASE_v*.md` for breaking changes
2. Re-test the `acent-ax-analysis` skill: `hermes /skill acent-ax-analysis` (or via ACP from `acent-flow`)
3. Run the FastAPI `tests/test_ax_analyze_route.py` against this engine
4. Update `acent/deployment/Dockerfile.acent` if upstream's base Dockerfile changed
5. Bump our overlay version note in `acent/README.md` if the contract changes

## Hot-fixing while a sync is pending

If you need an upstream fix that hasn't been merged yet:

```bash
git fetch upstream
git cherry-pick <upstream-sha>
```

Cherry-pick is fine for isolated upstream commits; reserve full merges for
periodic sync windows.

## What NOT to do

- **Do not edit upstream files directly.** If a fix is needed, contribute it
  upstream (or carry a clearly-flagged patch under `acent/patches/` with a
  Linear ticket linked to remove it).
- **Do not rename ACENT files into upstream paths.** That defeats the
  conflict-free property of the overlay structure.
- **Do not force-push `main`.** Merges must be linear-historable; force-push
  breaks the sync contract.
