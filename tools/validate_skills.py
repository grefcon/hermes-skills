#!/usr/bin/env python3
"""Validate every SKILL.md in this repo against the Hermes authoring conventions.

Dependency-free on purpose (no PyYAML) so CI needs nothing but a Python
interpreter. Checks, per skill:

  * frontmatter starts at byte 0 and closes with a ``---`` line
  * required keys present: name, description, version, author, license, platforms
  * description is one sentence, <= 60 chars, and ends with a period
    (the skill index truncates past 57 chars, so the trigger must fit inside it)
  * a ``## When to Use`` section exists
  * a ``## Verification`` section exists, or a sibling ``scripts/`` to lean on
  * every ``references/``, ``scripts/`` or ``templates/`` path named in the body
    actually resolves

Usage:  python3 tools/validate_skills.py [roots ...]      (default: skills)
Exit:   0 all valid, 1 at least one problem.
"""
from __future__ import annotations

import pathlib
import re
import sys

REQUIRED_KEYS = ("name", "description", "version", "author", "license", "platforms")
DESCRIPTION_LIMIT = 60
RELATIVE_REF = re.compile(r"`((?:references|scripts|templates)/[^`\s]+)`")
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][\w.-]*)\s*:\s*(.*)$")


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter, body), or None when the frontmatter is malformed."""
    if not text.startswith("---"):
        return None
    closing = re.search(r"\n---\s*\n", text[3:])
    if not closing:
        return None
    return text[3 : closing.start() + 3], text[closing.end() + 3 :]


def top_level_keys(frontmatter: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for line in frontmatter.splitlines():
        match = TOP_LEVEL_KEY.match(line)          # top level only: no leading space
        if match:
            keys[match.group(1)] = match.group(2).strip()
    return keys


def check(skill: pathlib.Path) -> list[str]:
    problems: list[str] = []
    text = skill.read_text(encoding="utf-8")

    parts = split_frontmatter(text)
    if parts is None:
        return ["frontmatter must start at byte 0 and close with a '---' line"]
    frontmatter, body = parts

    keys = top_level_keys(frontmatter)
    problems += [f"missing frontmatter key: {key}" for key in REQUIRED_KEYS if key not in keys]

    description = keys.get("description", "").strip().strip('"')
    if description:
        if len(description) > DESCRIPTION_LIMIT:
            problems.append(
                f"description is {len(description)} chars (limit {DESCRIPTION_LIMIT})"
            )
        if not description.endswith("."):
            problems.append("description must end with a period")

    if "## When to Use" not in body:
        problems.append("missing a '## When to Use' section")
    if "## Verification" not in body and not (skill.parent / "scripts").is_dir():
        problems.append("missing a '## Verification' section (and no scripts/ to lean on)")

    for reference in sorted(set(RELATIVE_REF.findall(body))):
        if not (skill.parent / reference).exists():
            problems.append(f"body references a missing file: {reference}")

    return problems


def main(roots: list[str]) -> int:
    skills: list[pathlib.Path] = []
    for root in roots:
        skills += sorted(pathlib.Path(root).rglob("SKILL.md"))

    if not skills:
        print(f"no SKILL.md found under: {', '.join(roots)}")
        return 1

    failed = 0
    for skill in skills:
        problems = check(skill)
        if problems:
            failed += 1
            print(f"FAIL {skill}")
            for problem in problems:
                print(f"       - {problem}")
        else:
            print(f"ok   {skill}")

    print(f"\n{len(skills) - failed}/{len(skills)} skill files valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["skills"]))
