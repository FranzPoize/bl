"""Identity and ownership of shared Odoo checkouts."""

import hashlib
import json
from dataclasses import asdict, dataclass


def content_id(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class OdooCheckoutSelection:
    modules: tuple[str, ...]
    locales: tuple[str, ...]

    def union(self, other: "OdooCheckoutSelection") -> "OdooCheckoutSelection":
        # Empty selections mean all modules/locales, not no coverage.
        def combine(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(sorted(set(left) | set(right))) if left and right else ()

        return OdooCheckoutSelection(combine(self.modules, other.modules), combine(self.locales, other.locales))

    def sparse_parameters(self) -> tuple[str, list[str]]:
        # Keep non-addon files available when broadening from locale-filtered
        # coverage to all locales, too.
        patterns = ["/*", "!/addons/*"]
        for module in self.modules or ("*",):
            base = f"/addons/{module}"
            patterns.append(f"{base}/*")
            if self.locales:
                patterns.append(f"!{base}/*/*.po")
                patterns.extend(f"{base}/*/{locale}.po" for locale in self.locales)
        return "--no-cone", patterns


@dataclass(frozen=True)
class OdooSourceRef:
    url: str
    ref: str
    kind: str


@dataclass(frozen=True)
class OdooWorktreeRecipe:
    repository_id: str
    ref: str
    pinned: bool
    format_version: int = 1
    merges: tuple[OdooSourceRef, ...] = ()
    patch_digests: tuple[str, ...] = ()
    ref_kind: str = ""

    @property
    def worktree_id(self) -> str:
        data = asdict(self)
        # Optional transformations do not affect the basic source identity.
        for key in ("merges", "patch_digests", "ref_kind"):
            if not data[key]:
                del data[key]
        return content_id(data)


@dataclass(frozen=True)
class OdooProjectBinding:
    target_path: str
    repository_id: str
    worktree_id: str

    @property
    def binding_id(self) -> str:
        return content_id(self.target_path)
