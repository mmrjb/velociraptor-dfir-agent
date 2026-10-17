"""Generate THIRD_PARTY_NOTICES.md for a release (developer tool).

    python scripts/third_party_notices.py --node-modules <dir> --wheelhouse <dir> --out <file>

* Frontend: production dependencies of frontend/package.json (bundled into the built UI) with
  their full license texts, read from node_modules.
* Python: runtime dependency wheels in the wheelhouse with the license metadata from each
  wheel's METADATA (each wheel also carries its own license files).
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from email.parser import Parser
from pathlib import Path

LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice)(\.(md|txt))?$", re.I)


def frontend_packages(node_modules: Path, package_json: Path) -> list[dict]:
    root = json.loads(package_json.read_text())
    seen: dict[str, dict] = {}
    queue = list(root.get("dependencies", {}))
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        pkg_dir = node_modules / name
        meta = json.loads((pkg_dir / "package.json").read_text())
        texts = [(f.name, f.read_text(errors="replace")) for f in sorted(pkg_dir.iterdir())
                 if f.is_file() and LICENSE_FILE_RE.match(f.name)]
        seen[name] = {"name": name, "version": meta.get("version"),
                      "license": meta.get("license"), "texts": texts,
                      "homepage": meta.get("homepage") or ""}
        queue.extend((meta.get("dependencies") or {}).keys())
        queue.extend((meta.get("peerDependencies") or {}).keys())
    return sorted(seen.values(), key=lambda p: p["name"])


def python_packages(wheelhouse: Path) -> list[dict]:
    out = {}
    for whl in sorted(wheelhouse.rglob("*.whl")):
        with zipfile.ZipFile(whl) as zf:
            meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
            msg = Parser().parsestr(zf.read(meta_name).decode("utf-8", "replace"))
        name = msg.get("Name")
        if not name or name.lower() == "velociraptor-dfir-agent":
            continue
        lic = msg.get("License-Expression") or ""
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in msg.get_all("Classifier") or []
                           if c.startswith("License ::")]
            lic = ", ".join(classifiers) or (msg.get("License") or "").splitlines()[0:1]
            lic = lic if isinstance(lic, str) else (lic[0] if lic else "see wheel")
        out[name.lower()] = {"name": name, "version": msg.get("Version"), "license": lic,
                             "home": msg.get("Home-page") or ""}
    return sorted(out.values(), key=lambda p: p["name"].lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node-modules", type=Path, required=True)
    ap.add_argument("--package-json", type=Path, required=True)
    ap.add_argument("--wheelhouse", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    lines = ["# Third-party notices", "",
             "## Components bundled in the built UI (`vr_dfir/web/static`)", ""]
    fe = frontend_packages(args.node_modules, args.package_json)
    for p in fe:
        lines.append(f"- {p['name']} {p['version']} - {p['license']}")
    lines += ["", "## Python dependencies (installed separately; wheels in `wheelhouse/`)", "",
              "Each wheel contains its own license files.", ""]
    for p in python_packages(args.wheelhouse):
        lines.append(f"- {p['name']} {p['version']} - {p['license']}")
    lines += ["", "## License texts of bundled UI components", ""]
    for p in fe:
        for fname, text in p["texts"]:
            lines += [f"### {p['name']} {p['version']} ({fname})", "", "```", text.strip(), "```", ""]
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({len(fe)} UI components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
