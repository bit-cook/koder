"""Integration tests for module wiring.

Tests that newly developed modules integrate cleanly:
- ForkContext builds from messages and can be used
- ToolOrchestrator partitions read/write calls correctly
- Onboarding state can be checked
- API errors get classified with user-friendly messages
- All modules import without circular dependencies
"""

import pytest


def test_fork_context_imports():
    """Test ForkContext can be imported and instantiated."""
    from koder_agent.tools.fork_agent import build_fork_context

    # Empty messages
    context = build_fork_context([])
    assert context is not None
    assert context.system_prompt is None
    assert context.conversation_messages == []


def test_fork_context_builds_from_messages():
    """Test ForkContext builds from sample messages."""
    from koder_agent.tools.fork_agent import build_fork_context

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    context = build_fork_context(messages)

    assert context.system_prompt == "You are a helpful assistant."
    assert len(context.conversation_messages) == 2
    assert context.conversation_messages[0]["role"] == "user"
    assert context.conversation_messages[1]["role"] == "assistant"


def test_fork_context_to_messages():
    """Test ForkContext can reconstruct full message list."""
    from koder_agent.tools.fork_agent import ForkContext

    ctx = ForkContext(
        system_prompt="Test system",
        conversation_messages=[
            {"role": "user", "content": "Test user"},
        ],
    )
    messages = ctx.to_messages()

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_tool_orchestrator_imports():
    """Test ToolOrchestrator can be imported and instantiated."""
    from koder_agent.tools.orchestration import ToolOrchestrator

    orchestrator = ToolOrchestrator()
    assert orchestrator is not None


def test_tool_orchestrator_partitions_correctly():
    """Test ToolOrchestrator separates read/write calls."""
    from koder_agent.tools.orchestration import ToolOrchestrator

    orchestrator = ToolOrchestrator()

    calls = [
        {"tool": "read_file", "args": {"path": "/tmp/a.txt"}},
        {"tool": "read_file", "args": {"path": "/tmp/b.txt"}},
        {"tool": "write_file", "args": {"path": "/tmp/c.txt"}},
        {"tool": "glob_search", "args": {"pattern": "*.py"}},
    ]

    batches = orchestrator.partition_calls(calls)

    # Should have: [read batch, write batch, read batch]
    assert len(batches) == 3
    assert batches[0].concurrent is True  # Two read_file
    assert len(batches[0].calls) == 2
    assert batches[1].concurrent is False  # One write_file
    assert len(batches[1].calls) == 1
    assert batches[2].concurrent is True  # One glob_search
    assert len(batches[2].calls) == 1


def test_tool_orchestrator_read_only_detection():
    """Test ToolOrchestrator identifies read-only tools."""
    from koder_agent.tools.orchestration import ToolOrchestrator

    orchestrator = ToolOrchestrator()

    assert orchestrator.is_read_only("read_file") is True
    assert orchestrator.is_read_only("list_directory") is True
    assert orchestrator.is_read_only("glob_search") is True
    assert orchestrator.is_read_only("grep_search") is True
    assert orchestrator.is_read_only("write_file") is False
    assert orchestrator.is_read_only("run_shell") is False


def test_onboarding_state_imports():
    """Test onboarding module can be imported."""
    from koder_agent.harness.onboarding import check_onboarding_state, get_onboarding_steps

    state = check_onboarding_state()
    assert state is not None
    assert hasattr(state, "completed")
    assert hasattr(state, "api_key_configured")

    steps = get_onboarding_steps(state)
    assert isinstance(steps, list)


def test_onboarding_incomplete_state():
    """Test onboarding identifies incomplete setup."""

    from koder_agent.harness.onboarding import check_onboarding_state, get_onboarding_steps

    # Check current state (may vary by environment)
    state = check_onboarding_state()

    # If not completed, should have steps
    if not state.completed:
        steps = get_onboarding_steps(state)
        assert len(steps) > 0
        assert all(isinstance(step, str) for step in steps)
    else:
        steps = get_onboarding_steps(state)
        assert len(steps) == 0


def test_api_error_classification_imports():
    """Test API error classification can be imported."""
    from koder_agent.agentic.api_errors import classify_api_error

    error = Exception("Test error")
    classified = classify_api_error(error)

    assert classified is not None
    assert classified.user_message is not None
    assert classified.original_error is error


def test_api_error_rate_limit():
    """Test rate limit error is classified correctly."""
    from koder_agent.agentic.api_errors import ApiErrorCategory, classify_api_error

    error = Exception("rate limit exceeded")
    classified = classify_api_error(error, status_code=429)

    assert classified.category == ApiErrorCategory.RATE_LIMIT
    assert classified.should_retry is True
    assert "Rate limit" in classified.user_message


def test_api_error_auth():
    """Test authentication error is classified correctly."""
    from koder_agent.agentic.api_errors import ApiErrorCategory, classify_api_error

    error = Exception("unauthorized: invalid api key")
    classified = classify_api_error(error, status_code=401)

    assert classified.category == ApiErrorCategory.AUTH
    assert classified.should_retry is False
    assert "API key" in classified.user_message


def test_api_error_context_overflow():
    """Test context length error is classified correctly."""
    from koder_agent.agentic.api_errors import ApiErrorCategory, classify_api_error

    error = Exception("context length exceeded maximum tokens")
    classified = classify_api_error(error)

    assert classified.category == ApiErrorCategory.CONTEXT_OVERFLOW
    assert classified.should_retry is False
    assert "context" in classified.user_message.lower()


def test_api_error_timeout():
    """Test timeout error is classified correctly."""
    from koder_agent.agentic.api_errors import ApiErrorCategory, classify_api_error

    error = TimeoutError("request timed out")
    classified = classify_api_error(error)

    assert classified.category == ApiErrorCategory.TIMEOUT
    assert classified.should_retry is True


def test_all_modules_import_together():
    """Test all integration modules can be imported simultaneously (no circular deps)."""
    from koder_agent.agentic.api_errors import classify_api_error
    from koder_agent.harness.onboarding import check_onboarding_state
    from koder_agent.tools.fork_agent import build_fork_context
    from koder_agent.tools.orchestration import ToolOrchestrator

    # All should be callable
    assert callable(build_fork_context)
    assert callable(check_onboarding_state)
    assert callable(classify_api_error)
    assert ToolOrchestrator() is not None


@pytest.mark.asyncio
async def test_orchestrator_execute_batch():
    """Test ToolOrchestrator can execute a batch of tool calls."""
    from koder_agent.tools.orchestration import ToolOrchestrator

    orchestrator = ToolOrchestrator()

    # Mock executor that tracks execution
    executed = []

    async def mock_executor(tool_name: str, args: dict):
        executed.append((tool_name, args))
        return f"result_{tool_name}"

    calls = [
        {"tool": "read_file", "args": {"path": "a.txt"}},
        {"tool": "read_file", "args": {"path": "b.txt"}},
    ]

    results = await orchestrator.execute_batch(calls, mock_executor)

    assert len(results) == 2
    assert results[0] == "result_read_file"
    assert results[1] == "result_read_file"
    assert len(executed) == 2


def test_fork_context_filters_incomplete_tool_calls():
    """Test ForkContext filters out incomplete tool calls."""
    from koder_agent.tools.fork_agent import build_fork_context

    messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Do something"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file"}}],
        },
        # No tool result for call_1 - should be filtered
        {"role": "user", "content": "Next message"},
    ]

    context = build_fork_context(messages)

    # Should filter out the incomplete tool call
    assert len(context.conversation_messages) == 2
    # First should be user message
    assert context.conversation_messages[0]["role"] == "user"
    # Tool call message should be filtered out or have no tool_calls
    assert context.conversation_messages[1]["role"] == "user"
