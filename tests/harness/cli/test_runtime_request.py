from koder_agent.harness.cli.entrypoint import build_runtime_request


def test_runtime_request_classifies_help_and_empty_boot():
    assert build_runtime_request(["--help"]).mode == "help"
    assert build_runtime_request([]).mode == "interactive"


def test_runtime_request_skips_teammate_mode_when_detecting_prompt_mode():
    request = build_runtime_request(["--teammate-mode", "in-process", "-p", "/peers"])

    assert request.mode == "prompt"
