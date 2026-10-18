"""Build an offline AI preview from a clean commit and a supplied cp312 wheelhouse.

Run with the development venv: python scripts/build_preview.py OUT --wheelhouse CP312_DIR
Frontend node_modules and Python build tools must already be installed. No index is used.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    kwargs.setdefault("cwd", ROOT)
    return subprocess.run(list(args), check=True, **kwargs)


def archive_release(stage: Path, archive_path: Path) -> str:
    """Validate the complete compressed stream before making the archive available."""
    with tempfile.TemporaryDirectory(prefix="preview-archive-", dir=archive_path.parent) as tmp:
        pending = Path(tmp) / archive_path.name
        with tarfile.open(pending, "x:gz") as archive:
            archive.add(stage, arcname=stage.name)
        with gzip.open(pending, "rb") as stream:
            while stream.read(1024 * 1024):
                pass  # Reads the trailer too, checking the gzip length and CRC.
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = hashlib.sha256(pending.read_bytes()).hexdigest()
        os.replace(pending, archive_path)
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--source-commit", help="Remote source commit when restored via an API")
    parser.add_argument("--source-tree", help="Remote Git tree SHA; must match the local tree")
    args = parser.parse_args()
    __version__ = runpy.run_path(str(ROOT / "src/vr_dfir/version.py"))["__version__"]
    name = f"velociraptor-dfir-ai-preview-{__version__}"
    if run("git", "status", "--porcelain", capture_output=True, text=True).stdout.strip():
        raise SystemExit("Commit the reviewed source before packaging.")
    tree = run("git", "rev-parse", "HEAD^{tree}", capture_output=True, text=True).stdout.strip()
    if bool(args.source_commit) != bool(args.source_tree):
        raise SystemExit("--source-commit and --source-tree must be supplied together.")
    if args.source_commit and (not re.fullmatch(r"[0-9a-f]{40}", args.source_commit)
                               or args.source_tree != tree):
        raise SystemExit("The remote source tree must exactly match the committed local tree.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / name
    # Refuse to remove or overwrite previous artifacts.
    stage.mkdir()
    (stage / "dist").mkdir()
    (stage / "source").mkdir()
    wheelhouse = stage / "wheelhouse" / "cp312"
    shutil.copytree(args.wheelhouse.resolve(), wheelhouse)
    run("npm", "run", "build", cwd=ROOT / "frontend")
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", str(stage / "dist"))
    wheel = next((stage / "dist").glob("*.whl"))
    with tempfile.TemporaryDirectory(prefix="vrdfir-resolution-") as probe:
        run(sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
            "--disable-pip-version-check", "--no-index", "--target", probe,
            "--find-links", str(wheelhouse), str(wheel) + "[ui,mcp]")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "vr_dfir/web/static/index.html" in names
        assert not any(n.startswith(("tests/", "vr_dfir/triage/", "vr_dfir/notebooks/",
                                     "vr_dfir/collection/")) for n in names)
    source = stage / "source" / "velociraptor-dfir-agent-src.tar.gz"
    with source.open("wb") as stream:
        run("git", "archive", "--format=tar.gz", "--prefix=velociraptor-dfir-agent/",
            "HEAD^{tree}", stdout=stream)
    for doc in ROOT.glob("*.md"):
        shutil.copy2(doc, stage / doc.name)
    shutil.copy2(ROOT / "NOTICE", stage)
    shutil.copy2(ROOT / ".env.example", stage)
    shutil.copytree(ROOT / "docs", stage / "docs")
    shutil.copy2(ROOT / "scripts/install-preview.sh", stage / "install-preview.sh")
    commit = args.source_commit or run("git", "rev-parse", "HEAD", capture_output=True,
                                      text=True).stdout.strip()
    (stage / "SOURCE_COMMIT.txt").write_text(commit + "\n")
    (stage / "SOURCE_TREE.txt").write_text(tree + "\n")
    archive_path = output / f"{name}.tar.gz"
    digest = archive_release(stage, archive_path)
    checksum = archive_path.with_name(archive_path.name + ".sha256")
    checksum.write_text(f"{digest}  {archive_path.name}\n")
    print(f"Built {archive_path}\n{digest}")


if __name__ == "__main__":
    main()
