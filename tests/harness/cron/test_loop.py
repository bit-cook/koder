"""Tests for the /loop cron command parser."""

import pytest

from koder_agent.harness.cron.loop import LoopSpecError, parse_loop_spec


def test_parse_raw_cron_with_prompt():
    spec = parse_loop_spec(["0", "9", "*", "*", "*", "morning", "standup"])

    assert spec.cron == "0 9 * * *"
    assert spec.prompt == "morning standup"
    assert spec.recurring is True


@pytest.mark.parametrize("prefix", ["once", "--once", "run-once"])
def test_parse_one_shot_raw_cron(prefix):
    spec = parse_loop_spec([prefix, "30", "14", "*", "*", "1", "monday", "review"])

    assert spec.cron == "30 14 * * 1"
    assert spec.prompt == "monday review"
    assert spec.recurring is False


def test_parse_every_minutes_alias():
    spec = parse_loop_spec(["@every", "5m", "check", "build"])

    assert spec.cron == "*/5 * * * *"
    assert spec.prompt == "check build"
    assert spec.recurring is True


def test_parse_bare_duration_alias():
    spec = parse_loop_spec(["5m", "check", "build"])

    assert spec.cron == "*/5 * * * *"
    assert spec.prompt == "check build"


def test_parse_every_seconds_alias_when_minute_aligned():
    spec = parse_loop_spec(["@every:300", "check", "build"])

    assert spec.cron == "*/5 * * * *"
    assert spec.prompt == "check build"


def test_rejects_sub_minute_every_alias():
    with pytest.raises(LoopSpecError, match="sub-minute"):
        parse_loop_spec(["@every", "30s", "too", "fast"])


def test_rejects_lossy_every_alias():
    with pytest.raises(LoopSpecError, match="cannot be represented"):
        parse_loop_spec(["@every", "45m", "uneven", "cadence"])


def test_rejects_non_ascii_every_seconds_without_bare_value_error():
    with pytest.raises(LoopSpecError, match="invalid @every seconds"):
        parse_loop_spec(["@every:³0", "bad", "digits"])


def test_rejects_after_turn_alias_for_durable_cron():
    with pytest.raises(LoopSpecError, match="@after-turn"):
        parse_loop_spec(["@after-turn", "follow", "up"])


def test_rejects_missing_prompt():
    with pytest.raises(LoopSpecError, match="prompt"):
        parse_loop_spec(["0", "9", "*", "*", "*"])


def test_rejects_invalid_cron_expression():
    with pytest.raises(LoopSpecError, match="Invalid cron expression"):
        parse_loop_spec(["invalid", "9", "*", "*", "*", "prompt"])
