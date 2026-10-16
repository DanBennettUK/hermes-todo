from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_release_zips.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_release_zips", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load()


class ReleaseZipTests(unittest.TestCase):
    def test_archives_match_the_tracked_tree_and_are_repeatable(self) -> None:
        version = release.read_version(ROOT)
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        names = [name for name in tracked.stdout.decode("utf-8").split("\0") if name]
        expected = [
            f"hermes-todo-v{version}/{name}"
            for name in sorted(names)
            if not name.startswith(".github/")
        ]
        self.assertTrue(expected)
        self.assertFalse(any("/.github/" in name or name.endswith("/.github") for name in expected))

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = release.build(ROOT, Path(first_dir))
            second = release.build(ROOT, Path(second_dir))
            self.assertEqual(first[0].read_bytes(), second[0].read_bytes())
            self.assertEqual(first[1].read_bytes(), second[1].read_bytes())

            bundle, desktop = first
            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(archive.namelist(), expected)
                for name in archive.namelist():
                    self.assertIsNone(release.forbidden_reason(name))
                    self.assertNotIn("node_modules", name)
            with zipfile.ZipFile(desktop) as archive:
                self.assertEqual(
                    archive.namelist(),
                    ["hermes-todo/plugin.js", "hermes-todo/README.md"],
                )
                readme = archive.read("hermes-todo/README.md")
                self.assertEqual(readme, (ROOT / "desktop-plugin" / "hermes-todo" / "README.md").read_bytes())

            for path in first:
                sidecar = path.with_name(path.name + ".sha256")
                digest, filename = sidecar.read_text(encoding="ascii").split("  ", 1)
                self.assertEqual(filename, path.name + "\n")
                self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_version_mismatch_refuses_to_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "server-plugin" / "hermes-todo"
            dashboard = plugin / "dashboard"
            dashboard.mkdir(parents=True)
            (plugin / "plugin.yaml").write_text("version: 0.2.0\n", encoding="utf-8")
            (dashboard / "manifest.json").write_text('{"version": "9.9.9"}\n', encoding="utf-8")
            with self.assertRaises(release.ReleaseZipError) as caught:
                release.read_version(root)
        self.assertIn("0.2.0", str(caught.exception))
        self.assertIn("9.9.9", str(caught.exception))

    def test_select_bundle_files_drops_ci_and_rejects_private_files(self) -> None:
        selected = release.select_bundle_files(
            ["README.md", ".github/workflows/tests.yml", "server-plugin/hermes-todo/plugin.yaml"]
        )
        self.assertEqual(
            selected,
            ["README.md", "server-plugin/hermes-todo/plugin.yaml"],
        )
        with self.assertRaises(release.ReleaseZipError):
            release.select_bundle_files([".env", "server-plugin/hermes-todo/todo.sqlite3"])
        with self.assertRaises(release.ReleaseZipError):
            release.select_bundle_files(["desktop-plugin/hermes-todo/node_modules/react/index.js"])
