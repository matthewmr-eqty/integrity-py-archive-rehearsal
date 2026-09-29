"""Checks for archive_release.sh and open_archive_pr.sh, run against a throwaway repository.

    python3 -m unittest scripts/test_archive_release.py

The render script is a stub, so this covers which releases are archived and what changes, not
the rendered pages. The reports are left uncommitted, as release.yml's downloads leave them. `gh`
is a stub that records its calls, and a bare repository stands in for GitHub. Not collected by
`just test-py`, which discovers under tests/ only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "archive_release.sh"
# Answers `gh pr list` with $FAKE_PRS, fails where asked, and records every call.
FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "pr list") [ -z "$FAKE_LIST_FAILS" ] || exit 1; echo "${FAKE_PRS:-[]}" ;;
  "workflow run") [ -z "$FAKE_DISPATCH_FAILS" ] || exit 1 ;;
  "pr create") while [ $# -gt 0 ]; do
      [ "$1" = --body-file ] && cp "$2" "$FAKE_GH_LOG.body"; shift; done ;;
esac
"""
REPORTS = [
    "auditwheel-show-linux-x86_64.txt",
    "auditwheel-show-linux-aarch64.txt",
    "otool-show-macos-arm64.txt",
    "otool-show-macos-x86_64.txt",
]
REDIRECT_TO_LATEST = {"source": "/(2\\.4\\.[0-9]+|latest|dev)/(.*)\\.html", "destination": "/$2/"}


class Repo:
    """A repository shaped like integrity-py's main, with release tags."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="archive-release-"))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("docs-site/src/content/docs/index.mdx", "latest\n")
        self.write("docs-site/archive/folders.json", "{}\n")
        self.write("scripts/render_api_docs.py", "")
        self.write("vercel.json", json.dumps({"redirects": [REDIRECT_TO_LATEST]}))
        shutil.copy(SCRIPT, self.root / "scripts/archive_release.sh")
        self.reports("main")
        self.commit("main")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def reports(self, version: str) -> None:
        for name in REPORTS:
            self.write(f"docs/generated/{name}", f"eqty_sdk-{version}-cp38-abi3-{name}\n")

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def release(self, version: str, with_docs: bool = True) -> None:
        """Tag a release. `with_docs=False` tags a tree without docs-site/, like 2.4.x."""
        if with_docs:
            self.git("tag", f"v{version}")
            return
        self.git("switch", "-q", "--detach")
        self.git("rm", "-q", "-r", "docs-site")
        self.commit(f"{version} without docs-site")
        self.git("tag", f"v{version}")
        self.git("switch", "-q", "main")

    def folders(self) -> dict:
        return json.loads((self.root / "docs-site/archive/folders.json").read_text())

    def changed(self, path: str) -> str:
        return self.git("status", "--porcelain", path)

    def run(self, version: str, python: str = sys.executable) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "scripts/archive_release.sh", version],
            cwd=self.root,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHON": python},
        )


class ArchiveRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(shutil.rmtree, self.repo.root, ignore_errors=True)

    def test_the_newest_release_is_archived_with_its_reports(self) -> None:
        self.repo.release("2.5.0")
        self.repo.reports("2.5.0")
        out = self.repo.run("2.5.0")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.repo.folders(), {"2.5.0": "archive/v2.5"})
        self.assertTrue((self.repo.root / "docs-site/archive/v2.5/index.mdx").is_file())
        self.assertIn("2.5.0", (self.repo.root / "docs/generated" / REPORTS[0]).read_text())
        self.assertIn("vercel.json sends /2.4.x/", out.stdout)

    def test_a_new_patch_replaces_its_minor_s_folder(self) -> None:
        self.repo.write("docs-site/archive/folders.json", json.dumps({"2.5.0": "archive/v2.5"}))
        self.repo.commit("2.5.0 archived")
        self.repo.release("2.5.1")
        self.repo.reports("2.5.1")
        out = self.repo.run("2.5.1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.repo.folders(), {"2.5.1": "archive/v2.5"})

    def test_an_older_patch_of_its_minor_changes_nothing_in_the_site(self) -> None:
        self.repo.release("2.5.0")
        self.repo.release("2.5.1")
        self.repo.reports("2.5.0")
        out = self.repo.run("2.5.0")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.repo.changed("docs-site"), "")

    def test_a_backport_is_archived_and_latest_keeps_its_own_reports(self) -> None:
        self.repo.write("docs-site/archive/folders.json", json.dumps({"2.4.2": "archive/v2.4"}))
        self.repo.commit("2.4.2 archived")
        self.repo.release("2.4.3")
        self.repo.release("2.5.0")
        self.repo.reports("2.4.3")
        out = self.repo.run("2.4.3")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.repo.folders(), {"2.4.3": "archive/v2.4"})
        self.assertEqual(self.repo.changed("docs/generated"), "")
        self.assertNotIn("vercel.json sends", out.stdout)

    def test_a_backport_leaves_latest_s_reports_even_when_its_own_are_staged(self) -> None:
        self.repo.write("docs-site/archive/folders.json", json.dumps({"2.4.2": "archive/v2.4"}))
        self.repo.commit("2.4.2 archived")
        self.repo.release("2.4.3")
        self.repo.release("2.5.0")
        self.repo.reports("2.4.3")
        self.repo.git("add", "docs/generated")
        out = self.repo.run("2.4.3")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.repo.changed("docs/generated"), "")

    def test_a_backport_whose_tag_has_no_docs_site_fails_and_names_the_fix(self) -> None:
        self.repo.write("docs-site/archive/folders.json", json.dumps({"2.4.2": "archive/v2.4"}))
        self.repo.commit("2.4.2 archived")
        self.repo.release("2.5.0")
        self.repo.release("2.4.3", with_docs=False)
        self.repo.reports("2.4.3")
        out = self.repo.run("2.4.3")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("2.4.2", out.stderr)
        self.assertEqual(self.repo.changed("docs-site"), "")

    def test_reports_from_another_version_are_refused(self) -> None:
        self.repo.release("2.5.0")
        out = self.repo.run("2.5.0")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.repo.changed("docs-site"), "")

    def test_a_version_with_no_tag_fails(self) -> None:
        self.repo.release("2.5.0")
        self.repo.reports("2.5.1")
        out = self.repo.run("2.5.1")
        self.assertNotEqual(out.returncode, 0)

    def test_no_versioned_redirect_left_to_retarget_is_not_a_failure(self) -> None:
        self.repo.write(
            "vercel.json",
            json.dumps(
                {"redirects": [{"source": "/(latest|dev)/(.*)\\.html", "destination": "/$2/"}]}
            ),
        )
        self.repo.commit("redirects retargeted")
        self.repo.release("2.5.0")
        self.repo.reports("2.5.0")
        out = self.repo.run("2.5.0")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_failed_render_fails_the_script(self) -> None:
        self.repo.release("2.5.0")
        self.repo.reports("2.5.0")
        out = self.repo.run("2.5.0", python="false")
        self.assertNotEqual(out.returncode, 0)


class OpenArchivePr(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repo()
        self.addCleanup(shutil.rmtree, self.repo.root, ignore_errors=True)
        self.remote = Path(tempfile.mkdtemp(prefix="archive-remote-"))
        self.addCleanup(shutil.rmtree, self.remote, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.repo.git("remote", "add", "origin", str(self.remote))
        shutil.copy(SCRIPTS / "open_archive_pr.sh", self.repo.root / "open_archive_pr.sh")
        bin_dir = self.repo.root.parent / (self.repo.root.name + "-bin")
        bin_dir.mkdir()
        self.addCleanup(shutil.rmtree, bin_dir, ignore_errors=True)
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        self.gh_log = bin_dir / "calls"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.gh_log),
            "GITHUB_REPOSITORY_OWNER": "eqtylab",
        }
        self.log = self.repo.root.parent / (self.repo.root.name + ".log")
        self.addCleanup(lambda: self.log.unlink(missing_ok=True))
        self.log.write_text("archive_release: 2.5.0 → docs-site/archive/v2.5\n")

    def archive(self) -> None:
        self.repo.write("docs-site/archive/v2.5/index.mdx", "2.5\n")

    def run_script(self, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "open_archive_pr.sh", "2.5.0", str(self.log)],
            cwd=self.repo.root,
            capture_output=True,
            text=True,
            env={**self.env, **env},
        )

    def calls(self) -> list:
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def pushed(self) -> bool:
        heads = subprocess.run(
            ["git", "branch", "--list", "docs/archive-v2.5.0"],
            cwd=self.remote,
            capture_output=True,
            text=True,
        )
        return bool(heads.stdout.strip())

    def test_nothing_archived_opens_nothing(self) -> None:
        self.repo.reports("2.5.0")
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.calls(), [])

    def test_it_pushes_opens_the_pr_then_starts_the_checks(self) -> None:
        self.archive()
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(self.pushed())
        verbs = [" ".join(c.split()[:2]) for c in self.calls()]
        self.assertEqual(verbs, ["pr list", "pr create", "workflow run", "workflow run"])
        self.assertIn("ci.yml", self.calls()[2])
        self.assertIn("docs-check.yml", self.calls()[3])

    def test_an_open_pr_is_left_alone_and_its_checks_restarted(self) -> None:
        self.archive()
        prs = json.dumps([{"headRepositoryOwner": {"login": "eqtylab"}}])
        out = self.run_script(FAKE_PRS=prs)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.pushed())
        self.assertFalse(any(c.startswith("pr create") for c in self.calls()))
        self.assertEqual(sum(c.startswith("workflow run") for c in self.calls()), 2)

    def test_an_open_pr_from_a_fork_does_not_count(self) -> None:
        self.archive()
        prs = json.dumps([{"headRepositoryOwner": {"login": "someone-else"}}])
        out = self.run_script(FAKE_PRS=prs)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(any(c.startswith("pr create") for c in self.calls()))

    def test_a_failed_dispatch_still_leaves_the_pr_open_and_warns(self) -> None:
        self.archive()
        out = self.run_script(FAKE_DISPATCH_FAILS="1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(any(c.startswith("pr create") for c in self.calls()))
        self.assertIn("::warning::", out.stdout)

    def test_a_failed_pr_lookup_fails_before_pushing(self) -> None:
        self.archive()
        out = self.run_script(FAKE_LIST_FAILS="1")
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse(self.pushed())

    def test_a_backport_pr_says_to_merge_promptly(self) -> None:
        self.archive()
        with self.log.open("a") as log:
            log.write("archive_release: backport; latest's pages and reports are unchanged.\n")
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stderr)
        body = Path(f"{self.gh_log}.body").read_text()
        self.assertTrue(body.startswith("A backport"))
        self.assertIn("Merge it promptly", body)


if __name__ == "__main__":
    unittest.main()
