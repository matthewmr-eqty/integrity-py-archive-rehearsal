"""Checks for archive_release.sh, run against a throwaway repository.

    python3 -m unittest scripts/test_archive_release.py

The render script is a stub, so this covers which releases are archived and what changes, not
the rendered pages. The reports are left uncommitted, as release.yml's downloads leave them. Not
collected by `just test-py`, which discovers under tests/ only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "archive_release.sh"
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

    def release(self, version: str, with_docs: bool = True, at: str = "HEAD") -> None:
        """Tag a release. `with_docs=False` tags a tree without docs-site/, like 2.4.x."""
        if with_docs:
            self.git("tag", f"v{version}", at)
            return
        self.git("switch", "-q", "--detach", at)
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


if __name__ == "__main__":
    unittest.main()
