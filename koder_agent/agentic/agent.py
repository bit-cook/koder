"""Agent definitions and hooks for Koder."""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import backoff
import litellm
from agents import Agent, ModelSettings
from agents.extensions.models.litellm_model import LitellmModel
from agents.items import ItemHelpers, ModelResponse, TResponseStreamEvent
from agents.models.openai_responses import Converter as ResponsesConverter
from agents.tracing import generation_span
from agents.usage import Usage
from agents.util._json import _to_dump_compatible
from openai import omit
from openai._models import construct_type
from openai.types.shared import Reasoning
from rich.console import Console

from ..auth.tool_utils import clean_json_schema
from ..config import get_config
from ..harness.agents.definitions import get_agent_definitions
from ..harness.reasoning_display import normalize_reasoning_display_mode
from ..mcp import MCPServerFactory, load_mcp_servers
from ..tools.skill import build_skills_metadata_prompt, discover_merged_skills
from ..utils.client import (
    GITHUB_COPILOT_HEADERS,
    LITELLM_RETRYABLE_ERRORS,
    get_model_client_snapshot,
)
from ..utils.model_info import get_maximum_output_tokens, should_use_reasoning_param
from ..utils.prompts import KODER_SYSTEM_PROMPT

console = Console()
logger = logging.getLogger(__name__)

# GITHUB_COPILOT_HEADERS is defined in ..utils.client (single source of truth)
# and re-imported above; the is_copilot branch below spreads it and appends a
# per-request x-request-id.

_BRIEF_MODE_INSTRUCTION = (
    "# Brief Mode\n"
    "You are in brief mode. Be extremely concise:\n"
    "- Give the shortest possible answer that is still correct and complete\n"
    "- Skip preambles, summaries, and sign-offs\n"
    "- No bullet lists when a single sentence suffices\n"
    "- For code changes, just make the change with minimal explanation\n"
    "- Only elaborate when the user explicitly asks for detail"
)


def _log_api_error_on_retry(details):
    """Log user-friendly error messages before retry attempts.

    Called by backoff decorator before each retry.
    """
    from ..agentic.api_errors import classify_api_error

    exc_info = details.get("exception")
    if exc_info is None:
        return

    # Extract status code if available
    status_code = None
    if hasattr(exc_info, "status_code"):
        status_code = exc_info.status_code
    elif hasattr(exc_info, "response") and hasattr(exc_info.response, "status_code"):
        status_code = exc_info.response.status_code

    classified = classify_api_error(exc_info, status_code=status_code)

    # Log user-friendly message
    if classified.should_retry:
        logger.info(
            "API error (will retry): %s [attempt %d/%d]",
            classified.user_message,
            details.get("tries", 0),
            details.get("max_tries", 0),
        )
    else:
        logger.error("API error (not retryable): %s", classified.user_message)


class RetryingLitellmModel(LitellmModel):
    """LitellmModel with backoff retry logic."""

    def _is_github_copilot(self) -> bool:
        """Check if the current model is using GitHub Copilot."""
        return "github_copilot" in str(self.model).lower()

    def _clean_tools_for_github_copilot(self, tools: list) -> list:
        """Clean tool schemas for GitHub Copilot compatibility.

        GitHub Copilot doesn't support $ref/$defs in JSON schemas.
        """
        if not tools or not self._is_github_copilot():
            return tools

        for tool in tools:
            if not hasattr(tool, "params_json_schema"):
                continue

            try:
                tool.params_json_schema = clean_json_schema(tool.params_json_schema)
                if hasattr(tool, "strict_json_schema"):
                    tool.strict_json_schema = False

            except Exception as exc:
                logger.debug(
                    "Failed to clean tool schema for %s: %s",
                    getattr(tool, "name", "unknown"),
                    exc,
                )

        return tools

    def _should_use_responses_api(self) -> bool:
        """
        GitHub Copilot Codex models are not accessible via /chat/completions.
        Route them through LiteLLM's Responses API instead.
        """
        model_lower = str(self.model).lower()
        return "github_copilot/" in model_lower and "codex" in model_lower

    async def _fetch_responses_api(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: ModelSettings,
        tools: list,
        output_schema,
        handoffs: list,
        *,
        previous_response_id: str | None,
        stream: bool,
        prompt: Any | None,
    ):
        if not hasattr(litellm, "aresponses"):
            raise RuntimeError(
                "GitHub Copilot Codex models require LiteLLM Responses API support. "
                "Please upgrade litellm to a version that provides `aresponses`."
            )
        list_input = ItemHelpers.input_to_new_input_list(input)
        list_input = _to_dump_compatible(list_input)

        if model_settings.parallel_tool_calls and tools:
            parallel_tool_calls: bool | None = True
        elif model_settings.parallel_tool_calls is False:
            parallel_tool_calls = False
        else:
            parallel_tool_calls = None

        tool_choice = ResponsesConverter.convert_tool_choice(model_settings.tool_choice)
        if tool_choice is omit:
            tool_choice = None

        converted_tools = ResponsesConverter.convert_tools(tools, handoffs)
        tools_payload = _to_dump_compatible(converted_tools.tools)
        if not tools_payload:
            tools_payload = None

        text_param = ResponsesConverter.get_response_format(output_schema)
        if text_param is omit:
            text_param = None

        include_set = set(converted_tools.includes)
        response_include = getattr(model_settings, "response_include", None)
        if response_include is not None:
            include_set.update(response_include)
        top_logprobs = getattr(model_settings, "top_logprobs", None)
        if top_logprobs is not None:
            include_set.add("message.output_text.logprobs")
        include = list(include_set) if include_set else None

        extra_args: dict[str, Any] = dict(model_settings.extra_args or {})
        if top_logprobs is not None:
            extra_args["top_logprobs"] = top_logprobs
        verbosity = getattr(model_settings, "verbosity", None)
        if verbosity is not None:
            if text_param is not None and isinstance(text_param, dict):
                text_param["verbosity"] = verbosity
            else:
                text_param = {"verbosity": verbosity}

        aresponses_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": list_input,
            "include": include,
            "instructions": system_instructions,
            "tools": tools_payload,
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
            "temperature": model_settings.temperature,
            "top_p": model_settings.top_p,
            "truncation": getattr(model_settings, "truncation", None),
            "max_output_tokens": model_settings.max_tokens,
            "reasoning": getattr(model_settings, "reasoning", None),
            "metadata": model_settings.metadata,
            "previous_response_id": previous_response_id,
            "prompt": prompt,
            "stream": stream,
            "extra_headers": self._merge_headers(model_settings),
            "extra_query": model_settings.extra_query,
            "extra_body": model_settings.extra_body,
            **extra_args,
        }
        if self.api_key:
            aresponses_kwargs["api_key"] = self.api_key
        if self.base_url:
            aresponses_kwargs["base_url"] = self.base_url

        return await litellm.aresponses(**aresponses_kwargs)

    @backoff.on_exception(
        backoff.expo,
        LITELLM_RETRYABLE_ERRORS,
        max_tries=3,
        jitter=backoff.full_jitter,
        on_backoff=_log_api_error_on_retry,
    )
    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: ModelSettings,
        tools: list,
        output_schema,
        handoffs: list,
        tracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,  # unused for LiteLLM responses
        prompt: Any | None = None,
    ) -> ModelResponse:
        # Clean tools for GitHub Copilot compatibility
        cleaned_tools = self._clean_tools_for_github_copilot(tools)

        if not self._should_use_responses_api():
            return await super().get_response(
                system_instructions,
                input,
                model_settings,
                cleaned_tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )

        with generation_span(
            model=str(self.model),
            model_config=model_settings.to_json_dict()
            | {"base_url": str(self.base_url or ""), "model_impl": "litellm-responses"},
            disabled=tracing.is_disabled(),
        ) as span_generation:
            response = await self._fetch_responses_api(
                system_instructions,
                input,
                model_settings,
                cleaned_tools,
                output_schema,
                handoffs,
                previous_response_id=previous_response_id,
                stream=False,
                prompt=prompt,
            )

            response_usage = getattr(response, "usage", None)
            if response_usage:
                usage_kwargs: dict[str, Any] = {
                    "requests": 1,
                    "input_tokens": getattr(response_usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(response_usage, "output_tokens", 0) or 0,
                    "total_tokens": getattr(response_usage, "total_tokens", 0) or 0,
                }
                usage = Usage(**usage_kwargs)
                span_generation.span_data.usage = {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                }
            else:
                usage = Usage()

            if tracing.include_data():
                try:
                    span_generation.span_data.output = (
                        [response.model_dump()] if hasattr(response, "model_dump") else [response]
                    )
                except Exception:
                    pass

        return ModelResponse(
            output=getattr(response, "output", []) or [],
            usage=usage,
            response_id=getattr(response, "id", None),
        )

    async def _stream_via_responses_api(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: ModelSettings,
        cleaned_tools: list,
        output_schema,
        handoffs: list,
        tracing,
        *,
        previous_response_id: str | None,
        prompt: Any | None,
    ):
        """Stream via the custom LiteLLM Responses API path.

        Yields ``TResponseStreamEvent`` objects identical to the original
        implementation. The caller is responsible for retry handling.
        """
        with generation_span(
            model=str(self.model),
            model_config=model_settings.to_json_dict()
            | {"base_url": str(self.base_url or ""), "model_impl": "litellm-responses"},
            disabled=tracing.is_disabled(),
        ) as span_generation:
            stream = await self._fetch_responses_api(
                system_instructions,
                input,
                model_settings,
                cleaned_tools,
                output_schema,
                handoffs,
                previous_response_id=previous_response_id,
                stream=True,
                prompt=prompt,
            )

            final_response = None
            async for chunk in stream:
                if hasattr(chunk, "model_dump"):
                    try:
                        data = chunk.model_dump()
                    except Exception:
                        data = chunk
                else:
                    data = chunk

                if isinstance(data, dict):
                    event_type = data.get("type")
                    if hasattr(event_type, "value"):
                        data["type"] = event_type.value
                    elif not isinstance(event_type, str):
                        data["type"] = str(event_type)
                    event = construct_type(value=data, type_=TResponseStreamEvent)
                else:
                    event = chunk

                if getattr(event, "type", None) == "response.completed":
                    final_response = getattr(event, "response", None)
                yield event

            if final_response is not None and getattr(final_response, "usage", None):
                usage_obj = final_response.usage
                span_generation.span_data.usage = {
                    "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
                    "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
                }
            if tracing.include_data() and final_response is not None:
                try:
                    span_generation.span_data.output = (
                        [final_response.model_dump()]
                        if hasattr(final_response, "model_dump")
                        else [final_response]
                    )
                except Exception:
                    pass

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list,
        model_settings: ModelSettings,
        tools: list,
        output_schema,
        handoffs: list,
        tracing,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,  # unused for LiteLLM responses
        prompt: Any | None = None,
    ):
        """Stream model output with retry-before-first-chunk semantics.

        ``backoff.on_exception`` cannot wrap an async-generator function: it
        only detects coroutine functions, so for an async generator it would
        return the generator object without ever wrapping the iteration, and
        retries would never fire. Instead we implement the retry loop manually
        here.

        A retryable error is only retried while NO chunk has been yielded
        downstream yet. Once any chunk has reached the consumer, retrying would
        replay partial output (duplicate tokens), so the error is re-raised
        instead.
        """
        # Clean tools for GitHub Copilot compatibility
        cleaned_tools = self._clean_tools_for_github_copilot(tools)

        max_tries = 5
        attempt = 0
        # backoff.expo yields 1, 2, 4, 8, ... seconds; full_jitter randomizes
        # each wait within [0, value]. We mirror that timing manually.
        wait_gen = backoff.expo()
        next(wait_gen)  # prime the generator (first value is the base)

        while True:
            attempt += 1
            yielded_any = False
            try:
                if self._should_use_responses_api():
                    source = self._stream_via_responses_api(
                        system_instructions,
                        input,
                        model_settings,
                        cleaned_tools,
                        output_schema,
                        handoffs,
                        tracing,
                        previous_response_id=previous_response_id,
                        prompt=prompt,
                    )
                else:
                    source = super().stream_response(
                        system_instructions,
                        input,
                        model_settings,
                        cleaned_tools,
                        output_schema,
                        handoffs,
                        tracing,
                        previous_response_id=previous_response_id,
                        conversation_id=conversation_id,
                        prompt=prompt,
                    )

                async for chunk in source:
                    yielded_any = True
                    yield chunk
                return
            except LITELLM_RETRYABLE_ERRORS as exc:
                # Once any chunk has been emitted downstream, a retry would
                # replay partial output. Propagate instead.
                if yielded_any or attempt >= max_tries:
                    raise
                _log_api_error_on_retry(
                    {
                        "exception": exc,
                        "tries": attempt,
                        "max_tries": max_tries,
                    }
                )
                wait = backoff.full_jitter(next(wait_gen))
                await asyncio.sleep(wait)
                continue


def _get_skills_metadata(config) -> str:
    """Load and return skills metadata from configured directories.

    Priority: project skills directory > user skills directory.
    Skills with the same name in project dir override user dir.
    """
    if os.environ.get("KODER_SIMPLE") == "1":
        return "Skills are disabled in bare mode."
    if not config.skills.enabled:
        return "Skills are disabled."

    all_skills = discover_merged_skills(
        cwd=Path.cwd(),
        user_dir=config.skills.user_skills_dir,
        project_dir=config.skills.project_skills_dir,
    )

    if not all_skills:
        return "No skills are currently available."

    return build_skills_metadata_prompt(all_skills)


def _get_environment_info(model_name: str) -> str:
    """Render environment facts for the {ENVIRONMENT_INFO} prompt placeholder."""
    import platform
    from datetime import date

    cwd = Path.cwd()
    lines = [
        f"Working directory: {cwd}",
        f"Is a git repository: {'true' if (cwd / '.git').exists() else 'false'}",
        f"Platform: {platform.system().lower()} ({platform.release()})",
        f"Today's date: {date.today().isoformat()}",
        f"Model: {model_name}",
        "When you need other model names or IDs (e.g. when building AI applications), "
        "verify them against documentation instead of guessing.",
    ]
    return "\n".join(lines)


def _get_agents_metadata() -> str:
    if os.environ.get("KODER_SIMPLE") == "1":
        return "Agents metadata is disabled in bare mode."
    try:
        definitions = get_agent_definitions(cwd=Path.cwd())
    except Exception:
        return "No agents are currently available."

    if not definitions.active_agents:
        return "No agents are currently available."

    lines = ["Available agents:", ""]
    for agent in sorted(definitions.active_agents, key=lambda item: item.agent_type.lower()):
        lines.append(f"- {agent.agent_type}: {agent.when_to_use}")
    return "\n".join(lines)


async def create_dev_agent(
    tools,
    *,
    name: str = "Koder",
    instructions_override: str | None = None,
    instructions_append: str | None = None,
    model_override: str | None = None,
    extra_mcp_server_configs=None,
) -> Agent:
    """Create the main development agent or an overridden subagent with MCP servers."""
    config = get_config()
    if os.environ.get("KODER_SIMPLE") == "1":
        mcp_servers = []
    else:
        mcp_servers = await load_mcp_servers()
    if extra_mcp_server_configs:
        if MCPServerFactory is None:
            raise RuntimeError("MCP transport dependencies are unavailable")
        mcp_servers = [
            *mcp_servers,
            *await MCPServerFactory.create_servers_from_configs(extra_mcp_server_configs),
        ]

    # Populate ToolSearch deferred tools registry with all available tools.
    # KODER_ENABLE_TOOL_SEARCH controls deferred loading behaviour:
    #   unset/true  — MCP tools deferred (names only), discovered via ToolSearch
    #   false       — all MCP tools loaded upfront, no deferral
    #   auto        — threshold mode: upfront if ≤10% of context, deferred otherwise
    #   auto:N      — threshold mode with custom % (e.g. auto:5)
    from koder_agent.tools.tool_search import _set_deferred_tools

    tool_search_mode = (
        (
            os.environ.get("KODER_ENABLE_TOOL_SEARCH")
            or os.environ.get("ENABLE_TOOL_SEARCH")
            or "true"
        )
        .strip()
        .lower()
    )

    all_deferred = list(tools)  # Start with regular tools
    # Start all MCP servers in parallel for faster initialization
    if mcp_servers:
        results = await asyncio.gather(
            *[server.list_tools() for server in mcp_servers], return_exceptions=True
        )
        for server, result in zip(mcp_servers, results):
            if isinstance(result, BaseException):
                server_name = getattr(server, "name", "unknown")
                logger.warning("MCP server '%s' failed to list tools: %s", server_name, result)
            else:
                all_deferred.extend(result)
    _set_deferred_tools(all_deferred if tool_search_mode != "false" else None)

    model_override_value = None if model_override in (None, "", "inherit") else str(model_override)
    model_client = get_model_client_snapshot(model_override_value)
    effective_model_name = model_client["model_name"]

    # Determine the model to use: native OpenAI string or LitellmModel instance.
    resolved_extra_headers = None
    if model_client["native_openai"]:
        # Use string model name for native OpenAI providers (handled by default client)
        model = effective_model_name
    else:
        # Use LitellmModel with explicit base_url and api_key
        litellm_kwargs = model_client["litellm_kwargs"]
        resolved_extra_headers = litellm_kwargs.get("extra_headers")
        model = RetryingLitellmModel(
            model=litellm_kwargs["model"],
            base_url=litellm_kwargs["base_url"],
            api_key=litellm_kwargs["api_key"],
        )

    # Build model_settings with reasoning if configured
    model_name_str = effective_model_name
    model_settings = ModelSettings(
        metadata={"source": "koder"},
        max_tokens=get_maximum_output_tokens(model_name_str),
    )
    reasoning_display = normalize_reasoning_display_mode(
        os.environ.get("KODER_REASONING_DISPLAY")
        or getattr(getattr(config, "harness", None), "reasoning_display", "off")
    )
    # Only set reasoning parameters for native OpenAI providers. LiteLLM-based providers
    # can expose reasoning through their own deltas, but the OpenAI Reasoning object can
    # cause schema validation errors when routed through LiteLLM.
    if (
        config.model.reasoning_effort is not None or reasoning_display != "off"
    ) and should_use_reasoning_param():
        reasoning_kwargs: dict[str, Any] = {"summary": "detailed"}
        if config.model.reasoning_effort is not None:
            reasoning_kwargs["effort"] = (
                None if config.model.reasoning_effort == "none" else config.model.reasoning_effort
            )
        model_settings.reasoning = Reasoning(**reasoning_kwargs)
    if resolved_extra_headers:
        model_settings.extra_headers = dict(resolved_extra_headers)

    # Build system prompt with skills metadata (Progressive Disclosure Level 1)
    skills_metadata = _get_skills_metadata(config)
    agents_metadata = _get_agents_metadata()
    environment_info = _get_environment_info(model_name_str)
    system_prompt = (
        instructions_override
        if instructions_override is not None
        else KODER_SYSTEM_PROMPT.replace("{SKILLS_METADATA}", skills_metadata)
        .replace("{AGENTS_METADATA}", agents_metadata)
        .replace("{ENVIRONMENT_INFO}", environment_info)
    )
    if instructions_append:
        system_prompt = f"{system_prompt.rstrip()}\n\n{instructions_append.strip()}"

    # Inject brief mode instruction when enabled
    from koder_agent.harness.config.service import RuntimeConfigService

    _harness_config = RuntimeConfigService().load().harness
    brief_enabled = (
        os.environ.get("KODER_BRIEF", "").lower() in ("1", "true")
        or _harness_config.brief_mode_enabled
    )
    if brief_enabled:
        system_prompt = f"{system_prompt.rstrip()}\n\n{_BRIEF_MODE_INSTRUCTION}"

    dev_agent = Agent(
        name=name,
        model=model,
        instructions=system_prompt,
        tools=tools,
        mcp_servers=mcp_servers,
        model_settings=model_settings,
    )

    if "github_copilot" in model_name_str:
        # NOTE: x-request-id is generated once here and reused for all requests
        # in this agent's lifetime. The openai-agents SDK applies extra_headers
        # statically — it does not support per-request dynamic header generation.
        # A truly rotating request id would require SDK-level support (e.g. a
        # callable header factory). This is a known limitation.
        dev_agent.model_settings.extra_headers = {
            **GITHUB_COPILOT_HEADERS,
            "x-request-id": str(uuid.uuid4()),
        }

    # planner.handoffs.append(dev_agent)
    return dev_agent
