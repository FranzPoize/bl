"""Identity and ownership of the basic shared Odoo checkouts."""

import hashlib
import json
from dataclasses import asdict, dataclass


def content_id(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class OdooCheckoutSelection:
    modules: tuple[str, ...]
    locales: tuple[str, ...]

    def sparse_parameters(self) -> tuple[str, list[str]]:
        if self.locales:
            patterns = ["/*", "!/addons/*"]
            for module in self.modules:
                base = f"/addons/{module}"
                patterns.extend([f"{base}/*", f"!{base}/*/*.po"])
                patterns.extend(f"{base}/*/{locale}.po" for locale in self.locales)
            return "--no-cone", patterns
        addons = [f"addons/{module}" for module in self.modules] if self.modules else ["addons"]
        return "--cone", [*addons, "debian", "doc", "odoo", "setup"]


@dataclass(frozen=True)
class OdooWorktreeRecipe:
    repository_id: str
    ref: str
    pinned: bool
    selection: OdooCheckoutSelection
    format_version: int = 1

    @property
    def worktree_id(self) -> str:
        return content_id(asdict(self))


@dataclass(frozen=True)
class OdooProjectBinding:
    target_path: str
    repository_id: str
    worktree_id: str

    @property
    def binding_id(self) -> str:
        return content_id(self.target_path)
