"""Named projects: a small registry mapping a name to a data dir.

A project is just a data dir (its own ``papers.db``, PDFs, text and exports) with a
name, so ``paperpipe --project NAME ...`` or ``paperpipe projects use NAME`` replaces
remembering paths. The storage model is unchanged: one single-writer SQLite file
per corpus (see docs/decisions/0001-projects-are-named-data-dirs.md).

The registry is one JSON file, ``$PAPERPIPE_PROJECTS`` or
``$XDG_CONFIG_HOME/paperpipe/projects.json`` (``~/.config/...`` by default).
Unregistering a project never touches its data dir.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProjectError(ValueError):
    """An unknown, invalid or duplicate project name."""


def registry_path() -> Path:
    explicit = os.environ.get("PAPERPIPE_PROJECTS")
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "paperpipe" / "projects.json"


def load() -> Dict[str, object]:
    path = registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as exc:
        raise ProjectError(f"project registry {path} is not valid JSON: {exc}") from exc
    data.setdefault("current", None)
    data.setdefault("projects", {})
    return data


def _save(data: Dict[str, object]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic: a crash mid-write never leaves a truncated registry


def add(name: str, path: Path, description: Optional[str] = None) -> Dict[str, object]:
    if not NAME_RE.match(name):
        raise ProjectError(
            f"invalid project name {name!r}: letters, digits, '.', '_' or '-', up to 64 chars"
        )
    data = load()
    if name in data["projects"]:
        raise ProjectError(f"project {name!r} already exists ({data['projects'][name]['path']})")
    entry = {
        "path": str(Path(path).expanduser().resolve()),
        "description": description or "",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data["projects"][name] = entry
    _save(data)
    return entry


def remove(name: str) -> Dict[str, object]:
    data = load()
    entry = data["projects"].pop(name, None)
    if entry is None:
        raise ProjectError(f"unknown project {name!r}")
    if data["current"] == name:
        data["current"] = None
    _save(data)
    return entry


def use(name: str) -> Dict[str, object]:
    data = load()
    if name not in data["projects"]:
        raise ProjectError(f"unknown project {name!r}")
    data["current"] = name
    _save(data)
    return data["projects"][name]


def path_of(name: str) -> Path:
    entry = load()["projects"].get(name)
    if entry is None:
        raise ProjectError(f"unknown project {name!r}; see `paperpipe projects list`")
    return Path(entry["path"])


def current() -> Optional[str]:
    return load()["current"]
