"""MCP server configuration management across user, local, and project scopes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional

import aiosqlite

from ..config import MCPLocalProjectConfigYaml, MCPServerConfigYaml, get_config_manager
from .server_config import MCPServerConfig, MCPServerScope, MCPServerType

logger = logging.getLogger(__name__)

# File-based locking: use fcntl on Unix, fall back to no-op on Windows/other platforms.
try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:  # pragma: no cover
    _HAS_FCNTL = False

_PROJECT_MCP_FILENAME = ".mcp.json"
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


class MCPServerManager:
    """Manages MCP server configurations in runtime-owned config files."""

    def __init__(self):
        self.config_manager = get_config_manager()
        self._migration_lock = asyncio.Lock()
        self._migration_completed = False

    @contextmanager
    def _config_lock(self) -> Generator[None, None, None]:
        """Acquire an exclusive file lock around config write operations.

        Uses fcntl.flock on Unix to prevent concurrent koder instances from
        clobbering each other's config writes. Falls back to a no-op on
        platforms where fcntl is unavailable (e.g. Windows).
        """
        if not _HAS_FCNTL:
            yield
            return

        lock_path = Path(str(self.config_manager.config_path) + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = lock_path.open("w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    @staticmethod
    def _legacy_db_path() -> Path:
        return Path.home() / ".koder" / "koder.db"

    @staticmethod
    def _cwd_path(cwd: str | Path | None = None) -> Path:
        return Path(cwd or os.getcwd()).resolve()

    @staticmethod
    def _project_config_path(cwd: str | Path | None = None) -> Path:
        return MCPServerManager._cwd_path(cwd) / _PROJECT_MCP_FILENAME

    @staticmethod
    def _normalize_scope(scope: MCPServerScope | str | None) -> MCPServerScope:
        if scope is None:
            return MCPServerScope.LOCAL
        if isinstance(scope, MCPServerScope):
            return scope
        return MCPServerScope(scope)

    @staticmethod
    def _scope_source_path(scope: MCPServerScope, cwd: str | Path | None = None) -> str:
        if scope == MCPServerScope.PROJECT:
            return str(MCPServerManager._project_config_path(cwd))
        return str(get_config_manager().config_path)

    @staticmethod
    def _project_candidate_paths(cwd: str | Path | None = None) -> list[Path]:
        root = MCPServerManager._cwd_path(cwd)
        candidates = [root, *root.parents]
        candidates.reverse()
        return [candidate / _PROJECT_MCP_FILENAME for candidate in candidates]

    @staticmethod
    def _path_contains(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    async def _ensure_legacy_migration(self) -> None:
        if self._migration_completed:
            return

        async with self._migration_lock:
            if self._migration_completed:
                return

            db_path = self._legacy_db_path()
            if not db_path.exists():
                self._migration_completed = True
                return

            try:
                async with aiosqlite.connect(db_path) as conn:
                    cursor = await conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_servers'"
                    )
                    if not await cursor.fetchone():
                        self._migration_completed = True
                        return

                    conn.row_factory = aiosqlite.Row
                    data_cursor = await conn.execute("SELECT * FROM mcp_servers")
                    rows = await data_cursor.fetchall()
            except Exception:
                self._migration_completed = True
                return

            if not rows:
                self._migration_completed = True
                return

            koder_config = self.config_manager.load()
            existing_names = {server.name for server in koder_config.mcp_servers}
            migrated = 0

            for row in rows:
                try:
                    config = MCPServerConfig.from_db_dict(dict(row))
                except Exception:
                    continue
                if config.name in existing_names:
                    continue
                koder_config.mcp_servers.append(self._mcp_config_to_yaml(config))
                existing_names.add(config.name)
                migrated += 1

            if migrated:
                self.config_manager.save(koder_config)

            self._migration_completed = True

    def _yaml_to_mcp_config(
        self,
        yaml_config: MCPServerConfigYaml,
        *,
        scope: MCPServerScope | None = None,
        source_path: str | None = None,
    ) -> MCPServerConfig:
        return MCPServerConfig(
            name=yaml_config.name,
            transport_type=MCPServerType(yaml_config.transport_type),
            command=yaml_config.command,
            args=yaml_config.args or [],
            env_vars=yaml_config.env_vars or {},
            url=yaml_config.url,
            headers=yaml_config.headers or {},
            cache_tools_list=yaml_config.cache_tools_list,
            allowed_tools=yaml_config.allowed_tools,
            blocked_tools=yaml_config.blocked_tools,
            scope=scope,
            source_path=source_path,
        )

    def _mcp_config_to_yaml(self, config: MCPServerConfig) -> MCPServerConfigYaml:
        return MCPServerConfigYaml(
            name=config.name,
            transport_type=config.transport_type.value,
            command=config.command,
            args=config.args or [],
            env_vars=config.env_vars or {},
            url=config.url,
            headers=config.headers or {},
            cache_tools_list=config.cache_tools_list,
            allowed_tools=config.allowed_tools,
            blocked_tools=config.blocked_tools,
        )

    def _local_scope_entry(
        self,
        cwd: str | Path | None,
        *,
        create: bool = False,
    ) -> MCPLocalProjectConfigYaml | None:
        koder_config = self.config_manager.load()
        project_root = str(self._cwd_path(cwd))
        for entry in koder_config.mcp_local_projects:
            if Path(entry.project_root).resolve() == Path(project_root):
                return entry
        if not create:
            return None
        entry = MCPLocalProjectConfigYaml(project_root=project_root, servers=[])
        koder_config.mcp_local_projects.append(entry)
        return entry

    def _load_user_servers(self) -> dict[str, MCPServerConfig]:
        koder_config = self.config_manager.load()
        return {
            server.name: self._yaml_to_mcp_config(
                server,
                scope=MCPServerScope.USER,
                source_path=str(self.config_manager.config_path),
            )
            for server in koder_config.mcp_servers
        }

    def _load_local_servers(self, cwd: str | Path | None = None) -> dict[str, MCPServerConfig]:
        koder_config = self.config_manager.load()
        cwd_path = self._cwd_path(cwd)
        matching_entries = [
            entry
            for entry in koder_config.mcp_local_projects
            if self._path_contains(Path(entry.project_root).resolve(), cwd_path)
        ]
        matching_entries.sort(key=lambda entry: len(Path(entry.project_root).parts))

        servers: dict[str, MCPServerConfig] = {}
        for entry in matching_entries:
            for server in entry.servers:
                servers[server.name] = self._yaml_to_mcp_config(
                    server,
                    scope=MCPServerScope.LOCAL,
                    source_path=str(self.config_manager.config_path),
                )
        return servers

    @staticmethod
    def _expand_env_string(value: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2)
            resolved = os.environ.get(name)
            if resolved is not None:
                return resolved
            if default is not None:
                return default
            raise ValueError(f"Missing required environment variable: {name}")

        return _ENV_VAR_PATTERN.sub(_replace, value)

    @classmethod
    def _expand_env_data(cls, value):
        if isinstance(value, str):
            return cls._expand_env_string(value)
        if isinstance(value, list):
            return [cls._expand_env_data(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._expand_env_data(item) for key, item in value.items()}
        return value

    def _config_from_mapping(
        self,
        name: str,
        mapping: dict,
        *,
        scope: MCPServerScope,
        source_path: str,
        expand_env: bool,
    ) -> MCPServerConfig:
        raw = self._expand_env_data(mapping) if expand_env else mapping
        transport = raw.get("type")
        if transport is None:
            if raw.get("command"):
                transport = MCPServerType.STDIO.value
            elif raw.get("url"):
                transport = MCPServerType.HTTP.value
        if transport is None:
            raise ValueError(f"MCP server '{name}' is missing a transport type")

        return MCPServerConfig(
            name=name,
            transport_type=MCPServerType(transport),
            command=raw.get("command"),
            args=raw.get("args") or [],
            env_vars=raw.get("env") or raw.get("env_vars") or {},
            url=raw.get("url"),
            headers=raw.get("headers") or {},
            headers_helper=raw.get("headersHelper") or raw.get("headers_helper"),
            oauth=raw.get("oauth"),
            cache_tools_list=bool(
                raw.get("cacheToolsList") or raw.get("cache_tools_list") or False
            ),
            allowed_tools=raw.get("allowedTools") or raw.get("allowed_tools"),
            blocked_tools=raw.get("blockedTools") or raw.get("blocked_tools"),
            scope=scope,
            source_path=source_path,
        )

    def _read_project_config(self, path: Path) -> dict:
        if not path.exists():
            return {"mcpServers": {}}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid MCP config at {path}: expected an object")
        raw_servers = raw.get("mcpServers")
        if raw_servers is None:
            return {"mcpServers": {}}
        if not isinstance(raw_servers, dict):
            raise ValueError(f"Invalid MCP config at {path}: mcpServers must be an object")
        return {"mcpServers": raw_servers}

    def _write_project_config(self, path: Path, servers: dict[str, dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mcpServers": servers}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load_project_servers(self, cwd: str | Path | None = None) -> dict[str, MCPServerConfig]:
        servers: dict[str, MCPServerConfig] = {}
        for path in self._project_candidate_paths(cwd):
            if not path.exists():
                continue
            raw = self._read_project_config(path)
            for name, mapping in raw["mcpServers"].items():
                servers[name] = self._config_from_mapping(
                    name,
                    mapping,
                    scope=MCPServerScope.PROJECT,
                    source_path=str(path),
                    expand_env=True,
                )
        return servers

    def _serialize_project_server(self, config: MCPServerConfig) -> dict:
        payload: dict[str, object] = {"type": config.transport_type.value}
        if config.command:
            payload["command"] = config.command
        if config.args:
            payload["args"] = list(config.args)
        if config.env_vars:
            payload["env"] = dict(config.env_vars)
        if config.url:
            payload["url"] = config.url
        if config.headers:
            payload["headers"] = dict(config.headers)
        if config.headers_helper:
            payload["headersHelper"] = config.headers_helper
        if config.oauth:
            payload["oauth"] = dict(config.oauth)
        if config.cache_tools_list:
            payload["cacheToolsList"] = True
        if config.allowed_tools:
            payload["allowedTools"] = list(config.allowed_tools)
        if config.blocked_tools:
            payload["blockedTools"] = list(config.blocked_tools)
        return payload

    def _effective_servers(self, cwd: str | Path | None = None) -> dict[str, MCPServerConfig]:
        user = self._load_user_servers()
        project = self._load_project_servers(cwd)
        local = self._load_local_servers(cwd)

        merged = dict(user)

        # Project (.mcp.json, lower trust) must NOT silently override a
        # user/global server of the same name — that would let a checked-in repo
        # file shadow the user's own trusted config. Keep the user entry and log
        # a visible warning on collision.
        for name, config in project.items():
            if name in user:
                logger.warning(
                    "MCP name collision: project server '%s' (from %s) is ignored "
                    "because a higher-trust user-scoped server with the same name exists.",
                    name,
                    config.source_path,
                )
                continue
            merged[name] = config

        # Local scope lives in the user's own config.yaml (mcp_local_projects),
        # so it is trusted and may intentionally override user/project entries.
        for name, config in local.items():
            if name in project and name not in user:
                logger.debug(
                    "MCP name collision: local server '%s' overrides the "
                    "project server of the same name.",
                    name,
                )
            merged[name] = config

        return merged

    async def add_server(
        self,
        config: MCPServerConfig,
        *,
        scope: MCPServerScope | str = MCPServerScope.LOCAL,
        cwd: str | Path | None = None,
    ) -> None:
        await self._ensure_legacy_migration()
        normalized_scope = self._normalize_scope(scope)
        if normalized_scope == MCPServerScope.USER:
            with self._config_lock():
                koder_config = self.config_manager.load()
                if any(server.name == config.name for server in koder_config.mcp_servers):
                    raise ValueError(f"MCP server already exists in user scope: {config.name}")
                koder_config.mcp_servers.append(self._mcp_config_to_yaml(config))
                self.config_manager.save(koder_config)
            return

        if normalized_scope == MCPServerScope.LOCAL:
            with self._config_lock():
                koder_config = self.config_manager.load()
                entry = self._local_scope_entry(cwd, create=True)
                assert entry is not None
                if any(server.name == config.name for server in entry.servers):
                    raise ValueError(f"MCP server already exists in local scope: {config.name}")
                entry.servers.append(self._mcp_config_to_yaml(config))
                self.config_manager.save(koder_config)
            return

        project_path = self._project_config_path(cwd)
        with self._config_lock():
            raw = self._read_project_config(project_path)
            if config.name in raw["mcpServers"]:
                raise ValueError(f"MCP server already exists in project scope: {config.name}")
            raw["mcpServers"][config.name] = self._serialize_project_server(config)
            self._write_project_config(project_path, raw["mcpServers"])

    async def import_json_server(
        self,
        name: str,
        json_payload: str,
        *,
        scope: MCPServerScope | str = MCPServerScope.LOCAL,
        cwd: str | Path | None = None,
    ) -> None:
        mapping = json.loads(json_payload)
        normalized_scope = self._normalize_scope(scope)
        config = self._config_from_mapping(
            name,
            mapping,
            scope=normalized_scope,
            source_path=self._scope_source_path(normalized_scope, cwd),
            expand_env=False,
        )
        await self.add_server(config, scope=normalized_scope, cwd=cwd)

    async def get_server(
        self,
        name: str,
        *,
        cwd: str | Path | None = None,
        scope: MCPServerScope | str | None = None,
    ) -> Optional[MCPServerConfig]:
        await self._ensure_legacy_migration()
        if scope is None:
            return self._effective_servers(cwd).get(name)

        normalized_scope = self._normalize_scope(scope)
        if normalized_scope == MCPServerScope.USER:
            return self._load_user_servers().get(name)
        if normalized_scope == MCPServerScope.LOCAL:
            return self._load_local_servers(cwd).get(name)
        return self._load_project_servers(cwd).get(name)

    async def list_servers(
        self,
        *,
        cwd: str | Path | None = None,
        scope: MCPServerScope | str | None = None,
    ) -> List[MCPServerConfig]:
        await self._ensure_legacy_migration()
        if scope is None:
            servers = self._effective_servers(cwd)
        else:
            normalized_scope = self._normalize_scope(scope)
            if normalized_scope == MCPServerScope.USER:
                servers = self._load_user_servers()
            elif normalized_scope == MCPServerScope.LOCAL:
                servers = self._load_local_servers(cwd)
            else:
                servers = self._load_project_servers(cwd)
        return [servers[name] for name in sorted(servers)]

    async def update_server(
        self,
        config: MCPServerConfig,
        *,
        scope: MCPServerScope | str | None = None,
        cwd: str | Path | None = None,
    ) -> bool:
        # Note: update_server delegates to remove_server + add_server which each
        # acquire their own _config_lock. This is safe because fcntl locks are
        # re-entrant within the same process (same fd not reused, each call opens
        # a fresh fd). For cross-process safety the remove+add is NOT atomic, but
        # this matches the pre-existing semantics.
        target_scope = scope or config.scope
        if target_scope is None:
            existing = await self.get_server(config.name, cwd=cwd)
            if existing is None:
                return False
            target_scope = existing.scope
        if not await self.remove_server(config.name, scope=target_scope, cwd=cwd):
            return False
        await self.add_server(config, scope=target_scope, cwd=cwd)
        return True

    async def remove_server(
        self,
        name: str,
        *,
        scope: MCPServerScope | str | None = None,
        cwd: str | Path | None = None,
    ) -> bool:
        await self._ensure_legacy_migration()
        target_scope = scope
        if target_scope is None:
            existing = await self.get_server(name, cwd=cwd)
            if existing is None:
                return False
            target_scope = existing.scope
        normalized_scope = self._normalize_scope(target_scope)

        if normalized_scope == MCPServerScope.USER:
            with self._config_lock():
                koder_config = self.config_manager.load()
                initial_count = len(koder_config.mcp_servers)
                koder_config.mcp_servers = [s for s in koder_config.mcp_servers if s.name != name]
                if len(koder_config.mcp_servers) < initial_count:
                    self.config_manager.save(koder_config)
                    return True
                return False

        if normalized_scope == MCPServerScope.LOCAL:
            with self._config_lock():
                koder_config = self.config_manager.load()
                entry = self._local_scope_entry(cwd, create=False)
                if entry is None:
                    return False
                initial_count = len(entry.servers)
                entry.servers = [server for server in entry.servers if server.name != name]
                if len(entry.servers) == initial_count:
                    return False
                self.config_manager.save(koder_config)
                return True

        project_path = self._project_config_path(cwd)
        with self._config_lock():
            raw = self._read_project_config(project_path)
            if name not in raw["mcpServers"]:
                return False
            del raw["mcpServers"][name]
            self._write_project_config(project_path, raw["mcpServers"])
            return True

    async def server_exists(
        self,
        name: str,
        *,
        cwd: str | Path | None = None,
        scope: MCPServerScope | str | None = None,
    ) -> bool:
        return await self.get_server(name, cwd=cwd, scope=scope) is not None

    async def get_servers_by_type(
        self,
        transport_type: str,
        *,
        cwd: str | Path | None = None,
        scope: MCPServerScope | str | None = None,
    ) -> List[MCPServerConfig]:
        normalized = MCPServerType(transport_type)
        servers = await self.list_servers(cwd=cwd, scope=scope)
        return [server for server in servers if server.transport_type == normalized]
