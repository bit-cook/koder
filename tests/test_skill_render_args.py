"""Tests for Skill.render_prompt positional-argument substitution (S3).

The old implementation replaced ``$0``, ``$1`` ... in ascending order with
``str.replace``, so ``$10`` became ``<value of $1>`` + ``0``. The fix does a
single regex pass over ``\\$ARGUMENTS[<n>]`` and bare ``\\$<n>`` placeholders so
multi-digit indices resolve to the correct argument.
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.tools.skill import Skill  # noqa: E402


def _make_skill(content: str) -> Skill:
    return Skill(name="args-skill", description="Args skill", content=content)


def test_multi_digit_positional_arg_resolves_correctly():
    """``$10`` maps to args[10], not ``<args[1]>0``."""
    args = [f"arg{i}" for i in range(12)]  # arg0 .. arg11
    skill = _make_skill("first=$1 tenth=$10 eleventh=$11")

    rendered = skill.render_prompt(args)

    assert "first=arg1" in rendered
    # With the old ascending str.replace loop, ``$10`` became ``arg1`` + ``0``
    # only when args[1]=="arg1" happened to reconstruct "arg10"; use distinct
    # values below to prove correct multi-digit lookup.
    assert "tenth=arg10" in rendered
    assert "eleventh=arg11" in rendered


def test_multi_digit_arg_not_corrupted_by_single_digit_prefix():
    """Distinct values prove ``$10`` is not built from ``$1`` + literal ``0``."""
    # args[1] = "AA", args[10] = "ZZ" -- the buggy loop would render "$10" as
    # "AA0", never "ZZ".
    args = ["a0", "AA", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "ZZ", "a11"]
    skill = _make_skill("one=$1 ten=$10")

    rendered = skill.render_prompt(args)

    assert "one=AA" in rendered
    assert "ten=ZZ" in rendered
    assert "AA0" not in rendered


def test_bracket_positional_arg_resolves_correctly():
    """``$ARGUMENTS[10]`` maps to args[10]."""
    args = [f"v{i}" for i in range(12)]
    skill = _make_skill("a=$ARGUMENTS[1] j=$ARGUMENTS[10] k=$ARGUMENTS[11]")

    rendered = skill.render_prompt(args)

    assert "a=v1" in rendered
    assert "j=v10" in rendered
    assert "k=v11" in rendered


def test_out_of_range_positional_arg_is_left_intact():
    """An index beyond the args list is left as-is (no crash, no wrong value)."""
    skill = _make_skill("x=$5")

    rendered = skill.render_prompt(["only-one"])

    assert "$5" in rendered


def test_single_digit_args_still_work():
    """Common case: single-digit positional args behave as before."""
    skill = _make_skill("$0 and $1")

    rendered = skill.render_prompt(["zero", "one"])

    assert "zero and one" in rendered


def test_arguments_placeholder_joins_all_args():
    """The bare $ARGUMENTS placeholder still joins all args."""
    skill = _make_skill("all: $ARGUMENTS")

    rendered = skill.render_prompt(["a", "b", "c"])

    assert "all: a b c" in rendered
