#!/usr/bin/env bash
# Open the PR that saves a release's archived docs, after archive_release.sh has run.
#
#     scripts/open_archive_pr.sh 2.5.0 "$RUNNER_TEMP/archive.log"
#
# Run by release.yml with GH_TOKEN and GITHUB_REPOSITORY_OWNER set. Safe to re-run: a branch a
# failed run pushed is rebuilt from main, and a branch whose PR is open is left as it is, with
# any commits on it; its checks are started again. Tests: scripts/test_archive_release.py.
set -euo pipefail

VERSION=${1:?usage: open_archive_pr.sh X.Y.Z ARCHIVE_LOG}
LOG=${2:?usage: open_archive_pr.sh X.Y.Z ARCHIVE_LOG}
OWNER=${GITHUB_REPOSITORY_OWNER:?GITHUB_REPOSITORY_OWNER is not set}
BRANCH="docs/archive-v$VERSION"

# A push made with github.token starts no workflows, and main requires CI's check, so start the
# checks by dispatch. A failed dispatch leaves the PR open with a warning, not a failed job.
start_checks() {
  for workflow in ci.yml docs-check.yml; do
    gh workflow run "$workflow" --ref "$BRANCH" ||
      echo "::warning::Could not start $workflow on $BRANCH; run it from the Actions tab."
  done
}

# The archive script leaves docs-site alone when there is nothing to archive. docs/generated can
# differ even then, since release.yml downloads the reports into it.
if [ -z "$(git status --porcelain docs-site)" ]; then
  exit 0
fi

# Only this repository's own branch counts; a fork's PR can share the branch name. An API error
# fails here rather than reading as "no PR".
open=$(gh pr list --head "$BRANCH" --state open --json headRepositoryOwner |
  jq --arg owner "$OWNER" '[.[] | select(.headRepositoryOwner.login == $owner)] | length')
if [ "$open" != 0 ]; then
  echo "The $VERSION archive PR is already open; left as it is."
  start_checks
  exit 0
fi

git switch --quiet -C "$BRANCH"
git add docs-site/archive docs/generated docs-site/src/content/docs
git -c user.name="github-actions[bot]" \
  -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit --quiet -m "docs: archive the $VERSION docs"
git push --force origin "$BRANCH"

BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT
{
  if grep -q '^archive_release: backport' "$LOG"; then
    echo "A backport: the v$VERSION tag now outranks the folder main's folders.json names, so main's docs build fails until this merges. Merge it promptly. latest's pages and reports are unchanged."
    echo
  fi
  echo "Saves the $VERSION docs, with the real wheel reports, as the version the site serves once a newer release ships."
  echo
  echo "Opened by release.yml with github.token, so no checks start on their own. The job starts CI and the docs build check on this branch; if either is missing, run it on the branch from the Actions tab, and check the Vercel preview."
  if grep -q '^archive_release: vercel.json' "$LOG"; then
    echo
    grep '^archive_release: vercel.json' "$LOG" | sed 's/^archive_release: /- [ ] /'
  fi
} >"$BODY"
gh pr create --base main --head "$BRANCH" \
  --title "docs: archive the $VERSION docs" \
  --body-file "$BODY"
start_checks
