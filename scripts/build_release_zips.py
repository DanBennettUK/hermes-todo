#!/usr/bin/env python3
"""Build the Hermes Todo release archives.

Writes the full bundle and the Desktop-only zip, each with a SHA-256 sidecar.
Does not create a git tag, a GitHub release, or an upload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPOCH = (1980, 1, 1, 0, 0, 0)
SKIP_PREFIXES = (".github/",)


class ReleaseZipError(Exception):
    """The working tree cannot be packed as a release."""


def read_version(root: Path) -> str:
    plugin_text = (root / "server-plugin" / "hermes-todo" / "plugin.yaml").read_text(
        encoding="utf-8"
    )
    plugin_version = ""
    for line in plugin_text.splitlines():
        if line.startswith("version:"):
            plugin_version = line.split(":", 1)[1].strip().strip("\"'")
            break
    if not plugin_version:
        raise ReleaseZipError("plugin.yaml has no version")
    manifest = json.loads(
        (root / "server-plugin" / "hermes-todo" / "dashboard" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_version = str(manifest.get("version") or "")
    if plugin_version != manifest_version:
        raise ReleaseZipError(
            f"version mismatch: plugin.yaml is {plugin_version}, "
            f"manifest.json is {manifest_version or '(missing)'}"
        )
    return plugin_version


def forbidden_reason(name: str) -> str | None:
    parts = name.split("/")
    if "node_modules" in parts:
        return "node_modules is not part of a release archive"
    base = parts[-1]
    if base == ".env" or base.startswith(".env."):
        return "env files are not part of a release archive"
    if ".sqlite" in base or base.endswith(".db"):
        return "database files are not part of a release archive"
    return None


def select_bundle_files(names: list[str]) -> list[str]:
    blocked = [name for name in names if forbidden_reason(name)]
    if blocked:
        raise ReleaseZipError("refusing to pack " + ", ".join(blocked))
    return sorted(name for name in names if not name.startswith(SKIP_PREFIXES))


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    names = [name for name in result.stdout.decode("utf-8").split("\0") if name]
    return select_bundle_files(names)


def _zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=archive_name, date_time=EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits |= 0x800
    return info


def write_zip(destination: Path, entries: list[tuple[str, bytes]]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for name, payload in entries:
            archive.writestr(_zip_info(name), payload)


def write_sidecar(zip_path: Path) -> None:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sidecar = zip_path.with_name(zip_path.name + ".sha256")
    sidecar.write_bytes(f"{digest}  {zip_path.name}\n".encode("ascii"))


def build(root: Path, out_dir: Path) -> list[Path]:
    version = read_version(root)
    prefix = f"hermes-todo-v{version}"
    bundle_entries = [
        (f"{prefix}/{relative}", (root / relative).read_bytes())
        for relative in tracked_files(root)
    ]
    desktop_root = root / "desktop-plugin" / "hermes-todo"
    desktop_entries = [
        ("hermes-todo/plugin.js", (desktop_root / "plugin.js").read_bytes()),
        ("hermes-todo/README.md", (desktop_root / "README.md").read_bytes()),
    ]
    bundle = out_dir / f"{prefix}.zip"
    desktop = out_dir / f"hermes-todo-desktop-v{version}.zip"
    write_zip(bundle, bundle_entries)
    write_zip(desktop, desktop_entries)
    write_sidecar(bundle)
    write_sidecar(desktop)
    return [bundle, desktop]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist",
        help="Directory for the zip files and sidecars (default: dist/)",
    )
    args = parser.parse_args(argv)
    try:
        written = build(ROOT, args.output)
    except ReleaseZipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
