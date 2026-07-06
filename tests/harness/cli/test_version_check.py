from __future__ import annotations

from koder_agent.harness import version_info


def test_is_newer_version():
    assert version_info.is_newer_version("1.2.0", "1.1.9") is True
    assert version_info.is_newer_version("1.1.0", "1.1.0") is False
    assert version_info.is_newer_version("1.0.0", "1.2.0") is False
    assert version_info.is_newer_version("2.0", "1.9.9") is True


def test_is_update_check_allowed_requires_interactive():
    assert version_info.is_update_check_allowed(interactive=False) is False


def test_is_update_check_allowed_ci_and_optout(monkeypatch):
    monkeypatch.setattr(version_info.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(version_info.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("CI", "1")
    assert version_info.is_update_check_allowed(interactive=True) is False

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(version_info.VERSION_CHECK_ENV_OPT_OUT, "1")
    assert version_info.is_update_check_allowed(interactive=True) is False


def test_is_update_check_allowed_true_when_interactive(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv(version_info.VERSION_CHECK_ENV_OPT_OUT, raising=False)
    monkeypatch.setattr(version_info.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(version_info.sys.stdout, "isatty", lambda: True)
    assert version_info.is_update_check_allowed(interactive=True) is True


def test_is_update_check_allowed_false_without_tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv(version_info.VERSION_CHECK_ENV_OPT_OUT, raising=False)
    monkeypatch.setattr(version_info.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(version_info.sys.stdout, "isatty", lambda: True)
    assert version_info.is_update_check_allowed(interactive=True) is False


def test_version_cache_roundtrip(monkeypatch, tmp_path):
    cache = tmp_path / "version_check.json"
    monkeypatch.setattr(version_info, "_version_check_cache_path", lambda: cache)
    version_info._write_version_cache("9.9.9", now=1000.0)
    fresh = version_info._read_version_cache(now=1000.0 + 60)
    assert fresh is not None
    assert fresh["latest"] == "9.9.9"
    # Beyond TTL, cache is treated as stale.
    stale = version_info._read_version_cache(
        now=1000.0 + version_info.VERSION_CHECK_CACHE_TTL_SECONDS + 1
    )
    assert stale is None


def test_get_latest_version_uses_fresh_cache(monkeypatch, tmp_path):
    cache = tmp_path / "version_check.json"
    monkeypatch.setattr(version_info, "_version_check_cache_path", lambda: cache)
    version_info._write_version_cache("3.2.1", now=500.0)

    def fail_fetch(timeout=3.0):
        raise AssertionError("network should not be hit when cache is fresh")

    monkeypatch.setattr(version_info, "_fetch_latest_pypi_version", fail_fetch)
    latest = version_info.get_latest_version(now=500.0 + 10)
    assert latest == "3.2.1"


def test_get_latest_version_fetches_when_stale(monkeypatch, tmp_path):
    cache = tmp_path / "version_check.json"
    monkeypatch.setattr(version_info, "_version_check_cache_path", lambda: cache)
    monkeypatch.setattr(version_info, "_fetch_latest_pypi_version", lambda timeout=3.0: "4.5.6")
    latest = version_info.get_latest_version(force=True, now=999.0)
    assert latest == "4.5.6"
    # Result is cached for next time.
    cached = version_info._read_version_cache(now=999.0)
    assert cached["latest"] == "4.5.6"


def test_check_for_update_message(monkeypatch, tmp_path):
    cache = tmp_path / "version_check.json"
    monkeypatch.setattr(version_info, "_version_check_cache_path", lambda: cache)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv(version_info.VERSION_CHECK_ENV_OPT_OUT, raising=False)
    monkeypatch.setattr(version_info.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(version_info.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(version_info, "get_latest_version", lambda: "99.0.0")
    monkeypatch.setattr(
        version_info, "resolve_runtime_version_info", lambda: ("1.0.0", "installed-package")
    )
    message = version_info.check_for_update(interactive=True)
    assert message is not None
    assert "99.0.0" in message
    assert "koder upgrade" in message


def test_check_for_update_none_when_current(monkeypatch):
    monkeypatch.setattr(version_info, "is_update_check_allowed", lambda *, interactive: True)
    monkeypatch.setattr(version_info, "get_latest_version", lambda: "1.0.0")
    monkeypatch.setattr(
        version_info, "resolve_runtime_version_info", lambda: ("1.0.0", "installed-package")
    )
    assert version_info.check_for_update(interactive=True) is None


def test_check_for_update_none_when_disallowed(monkeypatch):
    monkeypatch.setattr(version_info, "is_update_check_allowed", lambda *, interactive: False)
    called = {"fetched": False}

    def should_not_fetch():
        called["fetched"] = True
        return "99.0.0"

    monkeypatch.setattr(version_info, "get_latest_version", should_not_fetch)
    assert version_info.check_for_update(interactive=True) is None
    assert called["fetched"] is False
