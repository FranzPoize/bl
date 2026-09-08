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
class OdooSourceRef:
    url: str
    ref: str
    kind: str


@dataclass(frozen=True)
class OdooWorktreeRecipe:
    repository_id: str
    ref: str
    pinned: bool
    selection: OdooCheckoutSelection
    format_version: int = 1
    merges: tuple[OdooSourceRef, ...] = ()
    patch_digests: tuple[str, ...] = ()
    ref_kind: str = ""

    @property
    def worktree_id(self) -> str:
        data = asdict(self)
        # Keep existing basic-branch identities so updating one consumer still
        # advances consumers created before patch/merge support was installed.
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
