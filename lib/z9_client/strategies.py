# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Modular colprof presets for chart build-profile.

Builtins loaded from lib/z9_client/data/colprof_strategies_builtin.toml
(versioned in the code for transparency). User overrides in
~/Documents/freeglaz/colprof_strategies.toml.

GUI-ready structure (label, flags, Markdown description, recommended_for,
tags) — directly consumable by the future GUI.
"""
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import tomllib
except ImportError:
    import tomli as tomllib

try:
    import tomli_w
except ImportError:
    tomli_w = None


@dataclass
class Strategy:
    name: str
    flags: str
    description: str = ""
    label: str = ""
    recommended_for: str = ""
    tags: list = field(default_factory=list)
    builtin: bool = False
    created: str = ""

    def to_dict(self):
        return asdict(self)


_BUILTIN_TOML = Path(__file__).parent / "data" / "colprof_strategies_builtin.toml"


def _load_builtin_strategies() -> dict[str, Strategy]:
    """Load the builtins from the versioned TOML file."""
    if not _BUILTIN_TOML.exists():
        logger.warning("Builtin strategies file not found: %s", _BUILTIN_TOML)
        return {}
    try:
        with open(_BUILTIN_TOML, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.error("Cannot load builtin strategies: %s", e)
        return {}
    strategies = {}
    for key, val in data.get("strategy", {}).items():
        strategies[key] = Strategy(
            name=key,
            flags=val.get("flags", ""),
            description=val.get("description", "").strip(),
            label=val.get("label", key),
            recommended_for=val.get("recommended_for", ""),
            tags=val.get("tags", []),
            builtin=True,
        )
    return strategies


def _user_strategies_path() -> Path:
    # Derived from the store root (cache.root_dir) -> a single source of truth for the path (honors
    # the FREEGLAZ_STORE_ROOT / config override; ready for the centralized rename step A).
    from . import cache
    return cache.root_dir() / "colprof_strategies.toml"


def _detect_argyll_ref() -> str:
    # Delegate to the centralized, configurable resolver (env FREEGLAZ_ARGYLL_REF/
    # ROOT > toml [argyll] > per-platform auto-detect). Keep a non-None string so
    # the flag substitution never yields "None"; if ref is genuinely absent, colprof
    # will fail with its own message (and `freeglaz check` flags it upfront).
    from . import argyll
    return argyll.find_argyll_ref_dir() or "/usr/share/color/argyll/ref"


def resolve_flags(strategy: Strategy) -> str:
    from . import cache
    flags = strategy.flags
    flags = flags.replace("{ARGYLL_REF}", _detect_argyll_ref())
    flags = flags.replace("{USER_HOME}", str(Path.home()))
    # Placeholder {FREEGLAZ_HOME}: NAME unchanged here (renamed in step A); only the VALUE now
    # comes from cache.root_dir() (centralized + honors the override).
    flags = flags.replace("{FREEGLAZ_HOME}", str(cache.root_dir()))
    return flags


class StrategyOps:

    def __init__(self):
        self._builtins = _load_builtin_strategies()

    @property
    def builtin_names(self) -> set:
        return set(self._builtins.keys())

    def list_strategies(self) -> list[Strategy]:
        merged = dict(self._builtins)
        user = self._load_user()
        merged.update(user)
        return sorted(merged.values(), key=lambda s: (not s.builtin, s.name))

    def get_strategy(self, name: str) -> Optional[Strategy]:
        user = self._load_user()
        if name in user:
            return user[name]
        return self._builtins.get(name)

    def create_user_strategy(self, name: str, flags: str,
                              description: str = "", label: str = "",
                              recommended_for: str = "", tags: Optional[list] = None) -> Strategy:
        if name in self._builtins:
            raise ValueError(f"Cannot override builtin strategy '{name}'. Choose a different name.")
        s = Strategy(
            name=name, flags=flags, description=description,
            label=label or name, recommended_for=recommended_for,
            tags=tags or [], builtin=False,
            created=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        user = self._load_user()
        user[name] = s
        self._save_user(user)
        return s

    def delete_user_strategy(self, name: str) -> None:
        if name in self._builtins:
            raise ValueError(f"Cannot delete builtin strategy '{name}'.")
        user = self._load_user()
        if name not in user:
            raise KeyError(f"User strategy '{name}' not found.")
        del user[name]
        self._save_user(user)

    def _load_user(self) -> dict[str, Strategy]:
        path = _user_strategies_path()
        if not path.exists():
            return {}
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return {}
        strategies = {}
        for key, val in data.get("strategy", {}).items():
            # Backward compat: label/recommended_for optional (defaults if absent)
            strategies[key] = Strategy(
                name=key,
                flags=val.get("flags", ""),
                description=val.get("description", ""),
                label=val.get("label", key),
                recommended_for=val.get("recommended_for", ""),
                tags=val.get("tags", []),
                builtin=False,
                created=val.get("created", ""),
            )
        return strategies

    def _save_user(self, strategies: dict[str, Strategy]) -> None:
        path = _user_strategies_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema_version": "1.0", "strategy": {}}
        for name, s in strategies.items():
            entry = {
                "label": s.label,
                "flags": s.flags,
                "description": s.description,
                "recommended_for": s.recommended_for,
                "tags": s.tags,
            }
            if s.created:
                entry["created"] = s.created
            data["strategy"][name] = entry
        if tomli_w:
            with open(path, "wb") as f:
                tomli_w.dump(data, f)
        else:
            # Fallback writer (basic, without full escaping)
            lines = ['schema_version = "1.0"']
            for name, s in strategies.items():
                lines.append(f'\n[strategy.{name}]')
                lines.append(f'label = {s.label!r}')
                lines.append(f'flags = {s.flags!r}')
                lines.append(f'description = """{s.description}"""')
                lines.append(f'recommended_for = {s.recommended_for!r}')
                lines.append(f'tags = {s.tags!r}')
                if s.created:
                    lines.append(f'created = {s.created!r}')
            path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


# Forward/backward compat: expose BUILTINS as a module-level attribute
# for code that might still import it (the tests, for example).
BUILTINS = _load_builtin_strategies()
