"""Loading, listing, and saving profiles.

Sources, in precedence order for a given profile ID:

1. User-owned profiles in ``<state_dir>/profiles`` (created by the UI or CLI).
2. Directories in ``VR_DFIR_PROFILE_DIRS``.
3. Profiles bundled with the package (read-only templates).

Bundled profiles are never rewritten: saving an edited template creates a user copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from vr_dfir import ids
from vr_dfir.errors import NotFoundError, ValidationError
from vr_dfir.jsonio import atomic_write_text, ensure_dir, sha256_json
from vr_dfir.profiles.models import Profile

MAX_PROFILE_BYTES = 256 * 1024


@dataclass
class ProfileRecord:
    profile: Profile
    origin: str  # bundled | user | directory | file
    path: str
    fingerprint: str

    @property
    def editable(self) -> bool:
        return self.origin == "user"

    def summary(self) -> dict:
        p = self.profile
        sources = 0
        for c in p.collections:
            sources += len(c.sources) if isinstance(c.sources, list) else 0
        return {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "purpose": p.purpose,
            "platforms": p.platforms,
            "collections": [
                {"id": c.id, "artifact": c.artifact, "required": c.required,
                 "sources": c.sources, "behavior_notes": c.behavior_notes}
                for c in p.collections
            ],
            "collection_count": len(p.collections),
            "declared_source_count": sources,
            "analyst_count": len(p.default_analysts()),
            "origin": self.origin,
            "editable": self.editable,
            "fingerprint": self.fingerprint,
            "validation": "valid (offline schema)",
        }


def format_pydantic_errors(exc: PydanticValidationError) -> list[dict]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        if err.get("type") == "extra_forbidden":
            msg = ("unknown field (profiles cannot contain execution-bearing or unsupported "
                   "fields)")
        out.append({"location": loc or "(profile)", "message": msg})
    return out


def parse_profile_text(text: str, *, source: str = "<text>") -> Profile:
    if len(text.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValidationError(f"Profile {source} is larger than {MAX_PROFILE_BYTES} bytes.")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(f"Profile {source} is not valid YAML/JSON: {exc}") from exc
    return parse_profile_data(data, source=source)


def parse_profile_data(data, *, source: str = "<data>") -> Profile:
    if not isinstance(data, dict):
        raise ValidationError(f"Profile {source} must be a mapping at the top level.")
    try:
        return Profile.model_validate(data)
    except PydanticValidationError as exc:
        issues = format_pydantic_errors(exc)
        raise ValidationError(
            f"Profile {source} failed validation ({len(issues)} issue(s)).",
            details={"issues": issues},
            next_action="Fix the listed fields and validate again.",
        ) from exc


def profile_fingerprint(profile: Profile) -> str:
    return sha256_json(profile.model_dump(mode="json"))


def dump_yaml(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude_defaults=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def bundled_profile_dir() -> Path | None:
    try:
        base = resources.files("vr_dfir") / "data" / "profiles"
        path = Path(str(base))
        if path.is_dir():
            return path
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    # Source checkout fallback: <repo>/profiles
    repo = Path(__file__).resolve().parents[3] / "profiles"
    return repo if repo.is_dir() else None


class ProfileStore:
    def __init__(self, state_dir: Path, extra_dirs: list[Path] | None = None):
        self.user_dir = Path(state_dir) / "profiles"
        self.extra_dirs = list(extra_dirs or [])

    def _candidates(self) -> list[tuple[str, Path]]:
        dirs: list[tuple[str, Path]] = []
        if self.user_dir.is_dir():
            dirs.append(("user", self.user_dir))
        for d in self.extra_dirs:
            if d.is_dir():
                dirs.append(("directory", d))
        bundled = bundled_profile_dir()
        if bundled:
            dirs.append(("bundled", bundled))
        return dirs

    def list(self) -> tuple[list[ProfileRecord], list[dict]]:
        """Return valid profiles (first occurrence of each ID wins) and load problems."""
        seen: dict[str, ProfileRecord] = {}
        problems = []
        for origin, directory in self._candidates():
            for path in sorted(directory.iterdir()):
                if path.suffix.lower() not in (".yaml", ".yml", ".json") or path.is_symlink():
                    continue
                try:
                    profile = parse_profile_text(path.read_text(encoding="utf-8"), source=path.name)
                except ValidationError as exc:
                    problems.append({"path": str(path), "origin": origin, **exc.to_dict()})
                    continue
                if profile.id in seen:
                    continue
                seen[profile.id] = ProfileRecord(profile, origin, str(path),
                                                 profile_fingerprint(profile))
        return sorted(seen.values(), key=lambda r: r.profile.id), problems

    def get(self, ref: str) -> ProfileRecord:
        """Load a profile by ID, or by path when ``ref`` names an existing file."""
        candidate = Path(ref).expanduser()
        if ref.endswith((".yaml", ".yml", ".json")) and candidate.is_file():
            profile = parse_profile_text(candidate.read_text(encoding="utf-8"), source=candidate.name)
            return ProfileRecord(profile, "file", str(candidate.resolve()),
                                 profile_fingerprint(profile))
        pid = ids.profile_id(ref)
        records, _ = self.list()
        for record in records:
            if record.profile.id == pid:
                return record
        raise NotFoundError(f"Profile {pid!r} was not found.",
                            next_action="Run 'vr-dfir profiles list' to see available profiles.")

    def save_user_profile(self, profile: Profile, *, overwrite: bool = False) -> ProfileRecord:
        """Save a user-owned profile. Bundled profile IDs cannot be overwritten."""
        records, _ = self.list()
        existing = {r.profile.id: r for r in records}
        if profile.id in existing and existing[profile.id].origin != "user":
            raise ValidationError(
                f"Profile ID {profile.id!r} belongs to a bundled template. Save it under a new ID.",
                next_action="Use Clone to create an editable copy.")
        if profile.id in existing and not overwrite:
            raise ValidationError(f"A user profile with ID {profile.id!r} already exists.")
        ensure_dir(self.user_dir)
        path = self.user_dir / f"{profile.id}.yaml"
        atomic_write_text(path, dump_yaml(profile))
        return ProfileRecord(profile, "user", str(path), profile_fingerprint(profile))

    def delete_user_profile(self, profile_id: str) -> None:
        pid = ids.profile_id(profile_id)
        path = self.user_dir / f"{pid}.yaml"
        if not path.is_file():
            raise NotFoundError(f"User profile {pid!r} does not exist.")
        path.unlink()

    def clone(self, source_id: str, new_id: str, new_name: str | None = None) -> ProfileRecord:
        record = self.get(source_id)
        data = json.loads(record.profile.model_dump_json())
        data["id"] = ids.profile_id(new_id)
        data["name"] = new_name or f"{record.profile.name} (copy)"
        profile = parse_profile_data(data, source=new_id)
        return self.save_user_profile(profile)
