---
applyTo: "**"
---

# Git push safety: always check PR status before pushing

## The problem

Pushing commits to a branch whose PR has already been merged means those commits
**never reach `master`**. They silently disappear. This has happened multiple
times in this repo (e.g. #252 → #253 → #254 chain).

## Mandatory pre-push checklist

Before **every** `git push` to an existing remote branch, run:

```sh
gh pr view <branch> --json state,mergedAt
```

### If the PR is **open** → push normally

```sh
git push
```

### If the PR is **merged** → STOP. Create a new branch and PR

```sh
# 1. Identify commits on the current branch not yet in master
git log --oneline origin/master..HEAD

# 2. Create a new branch from master
git checkout -b <descriptive-slug> origin/master

# 3. Cherry-pick the missing commits
git cherry-pick <sha> [<sha> ...]

# 4. Push and open a new PR
git push -u origin <descriptive-slug>
gh pr create --base master --title "..." --body "..."
```

Never push to a branch whose PR is already merged. Never.

## Automated check (combine with every push)

```sh
BRANCH=$(git branch --show-current)
PR_STATE=$(gh pr view "$BRANCH" --json state -q .state 2>/dev/null)
if [[ "$PR_STATE" == "MERGED" ]]; then
  echo "ERROR: PR for branch '$BRANCH' is already merged. Create a new branch."
  exit 1
fi
git push
```
