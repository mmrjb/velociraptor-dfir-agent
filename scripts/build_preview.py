"""Build an offline AI preview from a clean commit and a supplied cp312 wheelhouse.

Run with the development venv:
    python scripts/build_preview.py OUT --wheelhouse CP312_DIR --pip-wheel PIP_WHEEL
Frontend node_modules and Python build tools must already be installed. No index is used.

The dependency set is resolved offline for the deployment target (CPython 3.12.3 and glibc
2.39, as on Ubuntu 24.04 x86_64), pinned with SHA-256 hashes in requirements-cp312.txt, and
only those wheels are shipped. The pinned pip wheel lets the installer create its virtualenv
without ensurepip (Ubuntu's separate python3.12-venv package).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
TARGET = "linux-x86_64-cp312"
TAG = "cp312"
LOCK_NAME = f"requirements-{TAG}.txt"
EXTRAS = "mcp,ui"
# Ubuntu 24.04: CPython 3.12.3, glibc 2.39. Wheels must be manylinux (glibc >= 2.17 builds).
TARGET_PYTHON = "3.12.3"
TARGET_GLIBC_MINOR = 39
TARGET_PLATFORMS = [f"manylinux_2_{minor}_x86_64" for minor in range(TARGET_GLIBC_MINOR, 16, -1)]
TARGET_PLATFORMS += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]


def run(*args, **kwargs):
    kwargs.setdefault("cwd", ROOT)
    return subprocess.run(list(args), check=True, **kwargs)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def target_args() -> list[str]:
    args = ["--python-version", TARGET_PYTHON, "--implementation", "cp", "--abi", TAG]
    for platform in TARGET_PLATFORMS:
        args += ["--platform", platform]
    return args


def pip_offline(*args: str) -> list[str]:
    return [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
            "--isolated", "--disable-pip-version-check", "--no-cache-dir", "--no-index",
            "--only-binary=:all:", *args]


def resolved_wheels(report: dict) -> list[tuple[str, str, Path]]:
    """(name, version, local wheel) for every distribution in a pip installation report."""
    out = []
    for item in report["install"]:
        url = urlparse(item["download_info"]["url"])
        path = Path(unquote(url.path))
        if url.scheme != "file" or path.suffix != ".whl":
            raise SystemExit(f"Unexpected non-local or non-wheel distribution: {path.name}")
        out.append((item["metadata"]["name"], item["metadata"]["version"], path))
    return out


def lock_text(entries: list[tuple[str, str, str]]) -> str:
    """Hash-pinned pip requirements for (requirement name, version, sha256) entries."""
    lines = ["# Offline, hash-pinned dependency set for this release (pip --require-hashes).",
             f"# Resolved for CPython {TARGET_PYTHON} on Linux x86_64 with glibc "
             f"2.{TARGET_GLIBC_MINOR} (Ubuntu 24.04).", ""]
    for name, version, digest in sorted(entries, key=lambda e: e[0].lower()):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid SHA-256 for {name}")
        lines.append(f"{name}=={version} \\\n    --hash=sha256:{digest}")
    return "\n".join(lines) + "\n"


def archive_release(stage: Path, archive_path: Path) -> str:
    """Validate the complete compressed stream before making the archive available."""
    def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = 0o755 if info.isdir() or info.mode & 0o100 else 0o644
        return info

    with tempfile.TemporaryDirectory(prefix="preview-archive-", dir=archive_path.parent) as tmp:
        pending = Path(tmp) / archive_path.name
        with tarfile.open(pending, "x:gz") as archive:
            archive.add(stage, arcname=stage.name, filter=normalize)
        with gzip.open(pending, "rb") as stream:
            while stream.read(1024 * 1024):
                pass  # Reads the trailer too, checking the gzip length and CRC.
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = sha256(pending)
        os.replace(pending, archive_path)
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--pip-wheel", type=Path, required=True,
                        help="pip wheel the installer runs to bootstrap the virtualenv")
    parser.add_argument("--source-commit", help="Remote source commit when restored via an API")
    parser.add_argument("--source-tree", help="Remote Git tree SHA; must match the local tree")
    args = parser.parse_args()
    __version__ = runpy.run_path(str(ROOT / "src/vr_dfir/version.py"))["__version__"]
    name = f"velociraptor-dfir-ai-preview-{__version__}-{TARGET}"
    if run("git", "status", "--porcelain", capture_output=True, text=True).stdout.strip():
        raise SystemExit("Commit the reviewed source before packaging.")
    tree = run("git", "rev-parse", "HEAD^{tree}", capture_output=True, text=True).stdout.strip()
    if bool(args.source_commit) != bool(args.source_tree):
        raise SystemExit("--source-commit and --source-tree must be supplied together.")
    if args.source_commit and (not re.fullmatch(r"[0-9a-f]{40}", args.source_commit)
                               or args.source_tree != tree):
        raise SystemExit("The remote source tree must exactly match the committed local tree.")
    pip_match = re.fullmatch(r"pip-([0-9][0-9.]*)-py3-none-any\.whl", args.pip_wheel.name)
    if not pip_match or not args.pip_wheel.is_file():
        raise SystemExit("--pip-wheel must be a pip-<version>-py3-none-any.whl file.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / name
    # Refuse to remove or overwrite previous artifacts.
    stage.mkdir()
    (stage / "dist").mkdir()
    (stage / "source").mkdir()
    wheelhouse = stage / "wheelhouse" / TAG
    wheelhouse.mkdir(parents=True)
    run("npm", "run", "build", cwd=ROOT / "frontend")
    run(sys.executable, "-m", "build", "--no-isolation", "--outdir", str(stage / "dist"))
    wheel = next((stage / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "vr_dfir/web/static/index.html" in names
        assert not any(n.startswith(("tests/", "vr_dfir/triage/", "vr_dfir/notebooks/",
                                     "vr_dfir/collection/")) for n in names)

    # Resolve for the target interpreter and platform, then ship only the resolved wheels.
    with tempfile.TemporaryDirectory(prefix="vrdfir-resolution-") as probe:
        report_path = Path(probe) / "report.json"
        run(*pip_offline("--target", str(Path(probe) / "t"), *target_args(),
                         "--find-links", str(args.wheelhouse.resolve()),
                         "--report", str(report_path), f"{wheel}[{EXTRAS}]"))
        report = json.loads(report_path.read_text())
    entries = []
    for dist_name, version, path in resolved_wheels(report):
        if path == wheel:
            entries.append((f"{dist_name}[{EXTRAS}]", version, sha256(wheel)))
            continue
        shutil.copy2(path, wheelhouse / path.name)
        entries.append((dist_name, version, sha256(wheelhouse / path.name)))
    if not any(e[0].startswith("velociraptor-dfir-agent[") for e in entries):
        raise SystemExit("The release wheel is missing from the resolution report.")
    shutil.copy2(args.pip_wheel, wheelhouse / args.pip_wheel.name)
    entries.append(("pip", pip_match.group(1), sha256(wheelhouse / args.pip_wheel.name)))
    (stage / LOCK_NAME).write_text(lock_text(entries))
    # The shipped lock must resolve, hash-checked, for the target and for this build host.
    for platform_args in (target_args(), []):
        with tempfile.TemporaryDirectory(prefix="vrdfir-lock-check-") as probe:
            run(*pip_offline("--require-hashes", "--target", probe, *platform_args,
                             "--find-links", str(wheelhouse), "--find-links",
                             str(stage / "dist"), "-r", str(stage / LOCK_NAME)))

    source = stage / "source" / "velociraptor-dfir-agent-src.tar.gz"
    with source.open("wb") as stream:
        run("git", "archive", "--format=tar.gz", "--prefix=velociraptor-dfir-agent/",
            "HEAD^{tree}", stdout=stream)
    # Only committed documents are shipped; local notes in the checkout never are.
    tracked = run("git", "ls-files", "-z", "--", "*.md", "docs", capture_output=True,
                  text=True).stdout.split("\0")
    for doc in filter(None, tracked):
        if "/" in doc and not doc.startswith("docs/"):
            continue
        (stage / doc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / doc, stage / doc)
    shutil.copy2(ROOT / "NOTICE", stage)
    shutil.copy2(ROOT / ".env.example", stage)
    for script in ("install-preview.sh", "preflight.sh", "preflight.py"):
        shutil.copy2(ROOT / "scripts" / script, stage / script)
    commit = args.source_commit or run("git", "rev-parse", "HEAD", capture_output=True,
                                      text=True).stdout.strip()
    (stage / "SOURCE_COMMIT.txt").write_text(commit + "\n")
    (stage / "SOURCE_TREE.txt").write_text(tree + "\n")
    frontend = json.loads((ROOT / "frontend/package.json").read_text())["version"]
    release = {
        "name": name, "version": __version__, "frontend_version": frontend,
        "source_commit": commit, "source_tree": tree,
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"os": "Linux", "machine": "x86_64", "python": "CPython 3.12",
                   "resolved_for": f"CPython {TARGET_PYTHON}, glibc 2.{TARGET_GLIBC_MINOR} "
                                   "(Ubuntu 24.04)",
                   "wheel_platforms": "manylinux, glibc >= 2.17"},
        "lock": LOCK_NAME, "wheelhouse": f"wheelhouse/{TAG}", "pip": pip_match.group(1),
        "packages": len(entries),
    }
    (stage / "RELEASE.json").write_text(json.dumps(release, indent=2) + "\n")
    files = sorted(p for p in stage.rglob("*") if p.is_file())
    (stage / "SHA256SUMS").write_text("".join(
        f"{sha256(p)}  {p.relative_to(stage).as_posix()}\n" for p in files))
    archive_path = output / f"{name}.tar.gz"
    digest = archive_release(stage, archive_path)
    checksum = archive_path.with_name(archive_path.name + ".sha256")
    checksum.write_text(f"{digest}  {archive_path.name}\n")
    print(f"Built {archive_path}\n{digest}")


if __name__ == "__main__":
    main()
