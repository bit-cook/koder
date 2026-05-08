from koder_agent.harness.cli.entrypoint import build_runtime_request


def test_auth_commands_bypass_runtime_rewrite_safely():
    assert build_runtime_request(["auth", "status"]).mode == "auth_passthrough"
