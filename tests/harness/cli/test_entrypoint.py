from koder_agent.harness.cli.entrypoint import build_runtime_request


def test_build_runtime_request_accepts_legacy_boot_context_without_executing_runtime():
    request = build_runtime_request(["--help"])
    assert request.argv == ["--help"]
    assert request.mode in {"help", "interactive", "prompt", "subcommand"}
