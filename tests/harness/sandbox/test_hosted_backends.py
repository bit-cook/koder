import types

import pytest

from koder_agent.harness.sandbox.backend import SandboxExecutionContext
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.sandbox.registry import (
    BACKEND_IDS,
    create_backend_client_and_options,
    get_backend_spec,
    get_backend_status,
)

HOSTED_BACKENDS = (
    "cloudflare",
    "e2b",
    "modal",
    "vercel",
)


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeOptions:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _patch_backend_module(monkeypatch, backend_id: str):
    from koder_agent.harness.sandbox import registry

    spec = get_backend_spec(backend_id)
    assert spec is not None
    module = types.SimpleNamespace(
        **{
            spec.client_class: _FakeClient,
            spec.options_class or "Options": _FakeOptions,
        }
    )

    def fake_import_module(name: str):
        if name == spec.module:
            return module
        if name == "docker":
            return types.SimpleNamespace(from_env=lambda: "docker-client")
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(registry.importlib, "import_module", fake_import_module)


def test_hosted_backend_status_reports_missing_credentials_without_secret_values(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_SANDBOX_WORKER_URL", raising=False)
    monkeypatch.setenv("CLOUDFLARE_SANDBOX_API_KEY", "secret-should-not-appear")

    status = get_backend_status("cloudflare")

    rendered = "\n".join((*status.credential_errors, status.reason, status.setup_hint or ""))

    assert status.available is False
    assert "CLOUDFLARE_SANDBOX_WORKER_URL" in rendered
    assert "secret-should-not-appear" not in rendered


def test_modal_backend_status_requires_local_auth(monkeypatch):
    from koder_agent.harness.sandbox import registry

    _patch_backend_module(monkeypatch, "modal")
    monkeypatch.setattr(registry, "_modal_auth_configured", lambda: False)

    status = get_backend_status("modal")

    assert status.available is False
    assert "Modal credentials" in status.reason


def test_modal_backend_status_accepts_local_auth(monkeypatch):
    from koder_agent.harness.sandbox import registry

    _patch_backend_module(monkeypatch, "modal")
    monkeypatch.setattr(registry, "_modal_auth_configured", lambda: True)

    status = get_backend_status("modal")

    assert status.available is True
    assert status.reason == "available"


def test_unknown_backend_status_is_actionable():
    status = get_backend_status("nope")

    assert status.available is False
    assert status.reason == "unknown backend"
    assert "unix-local" in (status.setup_hint or "")


@pytest.mark.parametrize("backend_id", HOSTED_BACKENDS)
def test_hosted_backend_status_reports_import_failures(backend_id, monkeypatch):
    from koder_agent.harness.sandbox import registry

    spec = get_backend_spec(backend_id)
    assert spec is not None

    def missing_import(name: str):
        if name == spec.module:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return types.SimpleNamespace()

    monkeypatch.setattr(registry.importlib, "import_module", missing_import)

    status = get_backend_status(backend_id)

    assert status.available is False
    assert status.dependency_errors
    assert spec.module in status.dependency_errors[0]


@pytest.mark.parametrize("backend_id", HOSTED_BACKENDS)
def test_hosted_backend_status_reports_missing_credentials(backend_id, monkeypatch):
    spec = get_backend_spec(backend_id)
    assert spec is not None
    if not spec.credential_groups:
        pytest.skip(f"{backend_id} uses provider-local auth instead of required env vars")
    for group in spec.credential_groups:
        for name in group:
            monkeypatch.delenv(name, raising=False)
    _patch_backend_module(monkeypatch, backend_id)

    status = get_backend_status(backend_id)

    assert status.available is False
    assert status.credential_errors
    assert any(group[0] in status.credential_errors[0] for group in spec.credential_groups)


@pytest.mark.parametrize(
    ("backend_id", "env", "client_kwargs", "options_kwargs"),
    (
        (
            "cloudflare",
            {
                "CLOUDFLARE_SANDBOX_WORKER_URL": "https://worker.example",
                "CLOUDFLARE_SANDBOX_API_KEY": "cf-token",
            },
            {},
            {"worker_url": "https://worker.example", "api_key": "cf-token"},
        ),
        (
            "e2b",
            {"E2B_API_KEY": "e2b-token", "KODER_SANDBOX_E2B_TYPE": "custom-type"},
            {},
            {"sandbox_type": "custom-type"},
        ),
        (
            "modal",
            {"KODER_SANDBOX_MODAL_APP_NAME": "koder-test"},
            {},
            {"app_name": "koder-test"},
        ),
        (
            "vercel",
            {
                "VERCEL_TOKEN": "vercel-token",
                "KODER_SANDBOX_VERCEL_PROJECT_ID": "project-123",
                "KODER_SANDBOX_VERCEL_TEAM_ID": "team-123",
            },
            {"token": "vercel-token"},
            {"project_id": "project-123", "team_id": "team-123"},
        ),
    ),
)
def test_hosted_backend_options_are_constructed(
    backend_id, env, client_kwargs, options_kwargs, monkeypatch
):
    _patch_backend_module(monkeypatch, backend_id)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    client, options = create_backend_client_and_options(backend_id)

    assert client.kwargs == client_kwargs
    assert options.kwargs == options_kwargs


def test_docker_backend_options_are_constructed(monkeypatch):
    _patch_backend_module(monkeypatch, "docker")
    monkeypatch.setenv("KODER_SANDBOX_DOCKER_IMAGE", "python:test")

    client, options = create_backend_client_and_options("docker")

    assert client.args == ("docker-client",)
    assert options.kwargs == {"image": "python:test"}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_id", HOSTED_BACKENDS)
async def test_hosted_backend_uses_generic_sdk_adapter(backend_id, tmp_path, monkeypatch):
    from koder_agent.harness.sandbox import sdk_backend

    calls: list[str] = []

    class FakeStatus:
        available = True
        reason = "available"
        unavailable_reasons = ()

    class FakeExecResult:
        stdout = b"hosted-ok"
        stderr = b""
        exit_code = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def exec(self, command, *, timeout, shell):
            calls.append(f"exec:{command}:{timeout}:{shell}")
            return FakeExecResult()

    class FakeClient:
        async def create(self, *, manifest, options):
            calls.append(f"create:{manifest.root}:{options}")
            return FakeSession()

        async def delete(self, session):
            calls.append(f"delete:{session.__class__.__name__}")

    monkeypatch.setattr(sdk_backend, "select_backend_id", lambda requested: requested)
    monkeypatch.setattr(sdk_backend, "get_backend_status", lambda *_a, **_k: FakeStatus())
    monkeypatch.setattr(
        sdk_backend,
        "create_backend_client_and_options",
        lambda requested: (FakeClient(), f"options:{requested}"),
    )

    result = await sdk_backend.execute_with_sdk_backend(
        SandboxExecutionContext(
            cwd=tmp_path,
            repo_root=tmp_path,
            command="printf hosted-ok",
            env={},
            timeout=5,
            background=False,
            session_id=None,
            policy=SandboxPolicy(mode="workspace-write", backend=backend_id),
        )
    )

    assert result.sandboxed is True
    assert result.backend_id == backend_id
    assert result.stdout == "hosted-ok"
    assert calls == [
        f"create:{tmp_path}:options:{backend_id}",
        "exec:printf hosted-ok:5:True",
        "delete:FakeSession",
    ]


def test_registry_still_covers_user_facing_backend_matrix():
    assert all(backend_id in BACKEND_IDS for backend_id in (*HOSTED_BACKENDS, "docker"))
