"""ULTRON skills: reusable versioned instruction bundles.

A skill is a named, versioned block of system-prompt instructions (plus an
informational list of tool kinds it expects) that a host can inject into any
agent prompt via ``skill_system_section``. Skills live as one TOML file per
skill under <ULTRON_HOME>/skills (default ~/.ultron/skills).

SELF-CONTAINED (LEGO rule): stdlib imports only — no core/, contracts/, tui/,
tools/, providers/ — so the package can be copied into any host as-is.
"""

from __future__ import annotations

from skills.store import (
    FileSkillStore,
    Skill,
    skill_system_section,
    validate_name,
)

__all__ = ["FileSkillStore", "Skill", "skill_system_section", "validate_name"]
