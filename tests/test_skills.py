"""Skill store tests: TOML round trips, name validation, content hashing,
malformed-file handling, and deterministic system-prompt injection (offline)."""

from __future__ import annotations

import pytest

from skills import FileSkillStore, Skill, skill_system_section, validate_name
from skills.store import default_skills_dir


def _skill(**overrides) -> Skill:
    fields = dict(
        name="web-research",
        description="Use when the task needs multi-source web research.",
        instructions="Search broadly first.\nThen verify each claim against a second source.",
        version="1.0.0",
        tools=("web_fetch",),
    )
    fields.update(overrides)
    return Skill(**fields)


# --- name validation ----------------------------------------------------------

def test_validate_name_accepts_and_rejects():
    assert validate_name("web-research") == "web-research"
    assert validate_name("a") == "a"
    assert validate_name("0x9") == "0x9"
    for bad in ("Bad Name", "../etc", "", "-leading", "under_score", "UPPER", "sp ace"):
        with pytest.raises(ValueError):
            validate_name(bad)


# --- store: persistence ---------------------------------------------------------

def test_default_dir_resolves_from_ultron_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    assert default_skills_dir() == tmp_path / "skills"
    store = FileSkillStore()  # no explicit dir -> <ULTRON_HOME>/skills
    assert store.dir == tmp_path / "skills"


def test_save_load_round_trip_all_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRON_HOME", str(tmp_path))
    store = FileSkillStore()
    store.save(_skill())     # save creates the dir on demand
    assert store.exists("web-research")
    loaded = store.load("web-research")
    assert loaded == _skill()  # frozen dataclass eq: name, description, instructions, version, tools
    assert loaded.tools == ("web_fetch",)


def test_round_trip_special_characters(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    nasty = 'Quote " backslash \\ newline\n\ttab unicode ünïcødé'
    store.save(_skill(name="nasty", instructions=nasty, description='Desc with "quotes"'))
    assert store.load("nasty").instructions == nasty


def test_read_defaults_for_missing_version_and_tools(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    (tmp_path / "skills").mkdir(parents=True)
    (tmp_path / "skills" / "minimal.toml").write_text(
        'name = "minimal"\ndescription = "d"\ninstructions = "i"\n', encoding="utf-8")
    skill = store.load("minimal")
    assert skill.version == "1.0.0" and skill.tools == ()


def test_overwrite_version_bump(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    v1 = _skill()
    store.save(v1)
    v2 = _skill(version="1.1.0", instructions="Updated instructions.")
    store.save(v2)  # overwrite allowed
    assert len(store.list()) == 1
    loaded = store.load("web-research")
    assert loaded == v2 and loaded != v1


# --- store: list / malformed -----------------------------------------------------

def test_list_sorted_and_malformed_skipped(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    assert store.list() == []  # missing dir is empty, not an error
    store.save(_skill(name="zeta", tools=()))
    store.save(_skill(name="alpha"))
    skills_dir = tmp_path / "skills"
    (skills_dir / "bad-syntax.toml").write_text("not [ valid toml =", encoding="utf-8")
    (skills_dir / "missing-fields.toml").write_text('name = "x"\n', encoding="utf-8")
    (skills_dir / "wrong-types.toml").write_text(
        'name = "y"\ndescription = 5\ninstructions = "i"\n', encoding="utf-8")
    assert [skill.name for skill in store.list()] == ["alpha", "zeta"]


def test_load_malformed_raises(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    store.save(_skill())
    path = tmp_path / "skills" / "web-research.toml"
    path.write_text("name = ", encoding="utf-8")  # invalid TOML syntax
    with pytest.raises(ValueError):
        store.load("web-research")
    path.write_text('name = "web-research"\ndescription = "d"\n', encoding="utf-8")  # fields missing
    with pytest.raises(ValueError):
        store.load("web-research")


def test_load_missing_raises(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    with pytest.raises(ValueError, match="not found"):
        store.load("nope")


def test_save_rejects_invalid_names(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    for bad in ("Bad Name", "../etc", ""):
        with pytest.raises(ValueError):
            store.save(_skill(name=bad))
    assert not (tmp_path / "skills").exists() or store.list() == []


# --- store: delete / exists -------------------------------------------------------

def test_delete_and_exists(tmp_path):
    store = FileSkillStore(tmp_path / "skills")
    store.save(_skill())
    assert store.exists("web-research") and not store.exists("other")
    assert not store.exists("Bad Name")  # invalid name is simply not a skill
    store.delete("web-research")
    assert not store.exists("web-research")
    with pytest.raises(ValueError):
        store.delete("web-research")


# --- identity ----------------------------------------------------------------------

def test_content_hash_stable_and_changes_with_content():
    a, b = _skill(), _skill()
    assert a.content_hash == b.content_hash       # same instructions -> same hash
    assert len(a.content_hash) == 16
    changed = _skill(instructions="Different instructions.")
    assert changed.content_hash != a.content_hash  # payload change is visible
    assert a.record_id() == f"web-research@1.0.0+{a.content_hash}"


# --- prompt injection ---------------------------------------------------------------

def test_skill_system_section_empty():
    assert skill_system_section([]) == ""


def test_skill_system_section_deterministic_and_complete():
    web = _skill()
    pdf = _skill(name="pdf-report", version="2.1.0", description="Use for PDF reports.")
    section = skill_system_section([web, pdf])
    assert section == skill_system_section([web, pdf])  # deterministic
    assert section.startswith("Active skills:")
    assert "=== skill: web-research (v1.0.0) ===" in section
    assert "=== skill: pdf-report (v2.1.0) ===" in section
    assert web.description in section and web.instructions in section
    assert pdf.instructions in section
    assert section.index("=== skill: web-research") < section.index("=== skill: pdf-report")
    flipped = skill_system_section([pdf, web])  # ordering as given, not sorted
    assert flipped.index("=== skill: pdf-report") < flipped.index("=== skill: web-research")
