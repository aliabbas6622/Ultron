"""File-backed skill store: reusable versioned instruction bundles.

Storage: one TOML file per skill at <dir>/<name>.toml (default
<ULTRON_HOME>/skills, ULTRON_HOME defaulting to ~/.ultron). Writing is
hand-serialized with json.dumps for string escaping (valid TOML basic
strings — same trick as providers/registry.py ``_toml_str``); reading uses
stdlib tomllib.

SELF-CONTAINED: stdlib imports only, so any host can reuse the package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def ultron_home() -> Path:
    return Path(os.environ.get("ULTRON_HOME", os.path.join(os.path.expanduser("~"), ".ultron")))


def default_skills_dir() -> Path:
    return ultron_home() / "skills"


def validate_name(name: str) -> str:
    """Return ``name`` if it matches ^[a-z0-9][a-z0-9-]*$, else raise ValueError."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid skill name {name!r}: must match ^[a-z0-9][a-z0-9-]*$")
    return name


@dataclass(frozen=True)
class Skill:
    name: str
    description: str            # one line: when this skill applies
    instructions: str           # multi-line system-prompt instructions
    version: str = "1.0.0"
    tools: tuple[str, ...] = ()  # tool kinds it expects, e.g. ("file_write", "web_fetch") — informational

    @property
    def content_hash(self) -> str:
        """sha256(instructions), first 16 hex chars — stable identity of the payload."""
        return hashlib.sha256(self.instructions.encode("utf-8")).hexdigest()[:16]

    def record_id(self) -> str:
        return f"{self.name}@{self.version}+{self.content_hash}"


class FileSkillStore:
    """CRUD over a directory of per-skill TOML files."""

    def __init__(self, dir: Path | None = None) -> None:
        self.dir = Path(dir) if dir is not None else default_skills_dir()

    # --- paths ---------------------------------------------------------------

    def _path(self, name: str) -> Path:
        # validates first, so a hostile name can never escape the skills dir
        return self.dir / f"{validate_name(name)}.toml"

    # --- CRUD ------------------------------------------------------------------

    def list(self) -> list[Skill]:
        """All skills from *.toml sorted by name; malformed files silently skipped."""
        if not self.dir.is_dir():
            return []
        skills: list[Skill] = []
        for path in sorted(self.dir.glob("*.toml")):
            try:
                skills.append(self._parse(path.read_text(encoding="utf-8")))
            except (ValueError, tomllib.TOMLDecodeError, OSError):
                continue
        return sorted(skills, key=lambda skill: skill.name)

    def load(self, name: str) -> Skill:
        path = self._path(name)
        if not path.is_file():
            raise ValueError(f"skill {name!r} not found")
        return self._parse(path.read_text(encoding="utf-8"))

    def save(self, skill: Skill) -> None:
        validate_name(skill.name)
        self.dir.mkdir(parents=True, exist_ok=True)
        lines = [
            f"name = {_toml_str(skill.name)}",
            f"description = {_toml_str(skill.description)}",
            f"version = {_toml_str(skill.version)}",
            f"tools = [{', '.join(_toml_str(tool) for tool in skill.tools)}]",
            f"instructions = {_toml_str(skill.instructions)}",
        ]
        self._path(skill.name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.is_file():
            raise ValueError(f"skill {name!r} not found")
        path.unlink()

    def exists(self, name: str) -> bool:
        try:
            return self._path(name).is_file()
        except ValueError:
            return False

    # --- parsing ----------------------------------------------------------------

    @staticmethod
    def _parse(text: str) -> Skill:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"malformed skill file: {exc}") from exc
        name = data.get("name")
        description = data.get("description")
        instructions = data.get("instructions")
        if not (isinstance(name, str) and isinstance(description, str) and isinstance(instructions, str)):
            raise ValueError("skill file must define string name, description and instructions")
        validate_name(name)  # an unusable internal name counts as malformed
        raw_tools = data.get("tools")
        if raw_tools is None:  # missing -> no expected tools
            raw_tools = []
        elif not isinstance(raw_tools, list) or not all(isinstance(tool, str) for tool in raw_tools):
            raise ValueError("skill tools must be a list of strings")
        version = data.get("version", "1.0.0")
        if not isinstance(version, str):
            raise ValueError("skill version must be a string")
        return Skill(name=name, description=description, instructions=instructions,
                     version=version, tools=tuple(raw_tools))


def skill_system_section(skills: list[Skill]) -> str:
    """Deterministic injection block for an agent system prompt.

    "" for no skills; otherwise an "Active skills:" header followed by one
    block per skill, ordering preserved exactly as given.
    """
    if not skills:
        return ""
    blocks = ["Active skills:"]
    for skill in skills:
        blocks.append(f"=== skill: {skill.name} (v{skill.version}) ===\n"
                      f"{skill.description}\n{skill.instructions}")
    return "\n".join(blocks)


def _toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping for our charset
    return json.dumps(value, ensure_ascii=False)
