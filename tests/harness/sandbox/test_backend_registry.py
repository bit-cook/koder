from koder_agent.harness.sandbox.registry import BACKEND_IDS, get_backend_statuses


def test_backend_registry_covers_user_facing_backends():
    assert BACKEND_IDS == ("unix-local", "docker", "cloudflare", "e2b", "modal", "vercel")


def test_backend_statuses_are_lazy_and_structured(monkeypatch):
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    statuses = get_backend_statuses("e2b")
    by_id = {status.backend_id: status for status in statuses}

    assert by_id["e2b"].selected is True
    assert by_id["e2b"].available is False
    assert any("E2B_API_KEY" in item for item in by_id["e2b"].credential_errors)
    assert by_id["unix-local"].capabilities.supports_shell is True
    assert by_id["unix-local"].capabilities.supports_network_policy == "unsupported"
