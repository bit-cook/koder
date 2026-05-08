"""Plugin marketplace registry with local and GitHub support."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .manifest import find_manifest, parse_manifest
from .name_validation import validate_plugin_name

_GITHUB_SHORTHAND = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")


@dataclass(frozen=True)
class MarketplaceSource:
    """A registered marketplace source."""

    name: str
    source_type: str  # "directory", "github", "git", "file"
    path: str  # local path (after clone) or original source


@dataclass(frozen=True)
class MarketplacePlugin:
    """A plugin available from a marketplace source."""

    name: str
    version: str
    description: str
    source: str  # marketplace name
    path: str  # local path to plugin directory


def _marketplace_cache_dir() -> Path:
    from koder_agent.harness.paths import harness_home_dir

    return harness_home_dir() / "plugins" / "marketplace-cache"


def _clone_github_repo(repo: str, target: Path) -> bool:
    """Clone a GitHub repo to target directory. Returns True on success."""
    url = f"https://github.com/{repo}.git"
    try:
        if target.exists():
            # Pull latest
            subprocess.run(
                ["git", "-C", str(target), "pull", "--ff-only"],
                capture_output=True,
                timeout=120,
                check=False,
            )
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _parse_marketplace_input(source: str) -> tuple[str, str, str]:
    """Parse a marketplace source string.

    Returns (source_type, name, resolved_path_or_source).
    """
    # GitHub shorthand: owner/repo
    if _GITHUB_SHORTHAND.match(source):
        parts = source.split("/")
        name = parts[-1]
        return "github", name, source

    # Git URL
    if source.startswith("https://") or source.startswith("git@"):
        # Derive name from URL
        name = source.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return "git", name, source

    # Local path
    path = Path(source).resolve()
    name = path.name
    return "directory", name, str(path)


class MarketplaceStore:
    """Reads/writes marketplace sources to a JSON file.

    Supports local directories and GitHub repositories.
    GitHub repos are cloned to ~/.koder/plugins/marketplace-cache/.
    """

    def __init__(self, store_path: Path):
        self._path = store_path

    @classmethod
    def default(cls) -> "MarketplaceStore":
        from koder_agent.harness.paths import harness_home_dir

        return cls(harness_home_dir() / "plugins" / "marketplaces.json")

    @classmethod
    def for_test(cls, root: Path) -> "MarketplaceStore":
        return cls(root / "marketplaces.json")

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, source_input: str) -> tuple[MarketplaceSource | None, str]:
        """Register a marketplace source.

        Accepts: owner/repo (GitHub), git URL, or local path.
        Returns (source, message). source is None on failure.
        """
        source_type, name, raw_source = _parse_marketplace_input(source_input)

        # Validate marketplace name
        is_valid, error_reason = validate_plugin_name(name, is_official=False)
        if not is_valid:
            return None, f"Invalid marketplace name: {error_reason}"

        local_path = raw_source
        if source_type == "github":
            cache = _marketplace_cache_dir() / name
            if not _clone_github_repo(raw_source, cache):
                return None, f"Failed to clone https://github.com/{raw_source}.git"
            local_path = str(cache)
        elif source_type == "git":
            cache = _marketplace_cache_dir() / name
            try:
                if cache.exists():
                    subprocess.run(
                        ["git", "-C", str(cache), "pull", "--ff-only"],
                        capture_output=True,
                        timeout=120,
                        check=False,
                    )
                else:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", raw_source, str(cache)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    if result.returncode != 0:
                        return None, f"Failed to clone {raw_source}"
                local_path = str(cache)
            except (subprocess.TimeoutExpired, OSError) as exc:
                return None, f"Git operation failed: {exc}"
        elif source_type == "directory":
            if not Path(local_path).is_dir():
                return None, f"Directory not found: {local_path}"

        data = self._load()
        data[name] = {
            "source_type": source_type,
            "path": local_path,
            "raw_source": raw_source,
        }
        self._save(data)
        return (
            MarketplaceSource(name=name, source_type=source_type, path=local_path),
            f"Added marketplace: {name}",
        )

    def remove(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def list_all(self) -> list[MarketplaceSource]:
        data = self._load()
        return [
            MarketplaceSource(
                name=name,
                source_type=entry.get("source_type", "directory"),
                path=entry.get("path", ""),
            )
            for name, entry in data.items()
        ]

    def get(self, name: str) -> MarketplaceSource | None:
        data = self._load()
        entry = data.get(name)
        if entry is None:
            return None
        return MarketplaceSource(
            name=name,
            source_type=entry.get("source_type", "directory"),
            path=entry.get("path", ""),
        )

    def discover_plugins(self, marketplace_name: str) -> list[MarketplacePlugin]:
        """List all plugins available from a registered marketplace.

        Scans immediate children of the marketplace root, and also common
        subdirectories like ``plugins/`` and ``external_plugins/`` where
        GitHub-hosted marketplaces typically nest their plugin directories.
        """
        source = self.get(marketplace_name)
        if source is None:
            return []
        source_path = Path(source.path)
        if not source_path.is_dir():
            return []

        plugins: list[MarketplacePlugin] = []
        seen_names: set[str] = set()

        # Directories to scan for plugin subdirs
        scan_roots = [source_path]
        for subname in ("plugins", "external_plugins"):
            candidate = source_path / subname
            if candidate.is_dir():
                scan_roots.append(candidate)

        for root in scan_roots:
            for subdir in sorted(root.iterdir()):
                if not subdir.is_dir() or subdir.name.startswith("."):
                    continue
                manifest_path = find_manifest(subdir)
                if manifest_path is None:
                    continue
                manifest, errors, _ = parse_manifest(subdir)
                if manifest is None or errors:
                    continue
                if manifest.name in seen_names:
                    continue
                seen_names.add(manifest.name)
                plugins.append(
                    MarketplacePlugin(
                        name=manifest.name,
                        version=manifest.version,
                        description=manifest.description,
                        source=marketplace_name,
                        path=str(subdir),
                    )
                )
        return plugins

    def find_plugin(self, plugin_id: str) -> MarketplacePlugin | None:
        """Find a plugin by name@marketplace identifier.

        If no @marketplace suffix, searches all marketplaces.
        """
        if "@" in plugin_id:
            name, marketplace = plugin_id.rsplit("@", 1)
            for plugin in self.discover_plugins(marketplace):
                if plugin.name == name:
                    return plugin
            return None

        # Search all marketplaces
        for source in self.list_all():
            for plugin in self.discover_plugins(source.name):
                if plugin.name == plugin_id:
                    return plugin
        return None
