"""Permission service for runtime tool calls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..sandbox.workspace import protected_write_violation, read_only_violation
from ..sandbox_settings import is_excluded_command, resolve_sandbox_settings
from .denial_log import DenialLog
from .modes import PermissionMode
from .path_policy import evaluate_path_access
from .persistence import PermissionStore
from .powershell_classifier import classify_powershell_command
from .results import PermissionEvaluationResult
from .rules import match_permission_rule, parse_permission_rule
from .shell_classifier import classify_shell_command

if TYPE_CHECKING:
    from .ai_classifier import AiShellClassifier
    from .rule_sources import RuleHierarchy


def _empty_rules() -> dict[str, dict[str, list[str]]]:
    return {}


@dataclass
class PermissionService:
    """Evaluates whether tool calls should run, ask, or deny."""

    mode: PermissionMode = PermissionMode.DEFAULT
    owner: str = "main"
    workspace_root: Path = field(default_factory=lambda: Path.cwd().resolve())
    additional_roots: list[Path] = field(default_factory=list)
    store: PermissionStore | None = None
    denial_log: DenialLog = field(default_factory=DenialLog)
    rules: dict[str, dict[str, list[str]]] = field(default_factory=_empty_rules)
    rule_hierarchy: "RuleHierarchy | None" = None
    _ai_classifier: "AiShellClassifier | None" = None

    def __post_init__(self) -> None:
        # Load rules from hierarchy if provided
        if self.rule_hierarchy is not None:
            effective_rules = self.rule_hierarchy.get_effective_rules()
            # Merge with existing rules (hierarchy takes precedence)
            for tool_name, behaviors in effective_rules.items():
                tool_rules = self.rules.setdefault(tool_name, {})
                for behavior, rule_list in behaviors.items():
                    bucket = tool_rules.setdefault(behavior, [])
                    for rule in rule_list:
                        if rule not in bucket:
                            bucket.append(rule)
        # Load from store if no rules and no hierarchy
        elif self.store is not None and not self.rules:
            self.rules = self.store.load().get("rules", {})

    @classmethod
    def default(
        cls,
        *,
        mode: PermissionMode = PermissionMode.DEFAULT,
        store: PermissionStore | None = None,
        denial_log: DenialLog | None = None,
        workspace_root: Path | str | None = None,
        owner: str = "main",
        rule_hierarchy: "RuleHierarchy | None" = None,
        ai_classifier: "AiShellClassifier | None" = None,
    ) -> "PermissionService":
        return cls(
            mode=mode,
            owner=owner,
            workspace_root=(
                Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
            ),
            store=store,
            denial_log=denial_log or DenialLog(),
            rule_hierarchy=rule_hierarchy,
            _ai_classifier=ai_classifier,
        )

    def export_rules(self) -> dict[str, dict[str, list[str]]]:
        return deepcopy(self.rules)

    def add_working_directory(self, path: Path | str) -> Path:
        normalized = Path(path).expanduser().resolve()
        if normalized != self.workspace_root and normalized not in self.additional_roots:
            self.additional_roots.append(normalized)
        return normalized

    def list_working_directories(self) -> list[Path]:
        return [self.workspace_root, *self.additional_roots]

    def add_rule(self, tool_name: str, behavior: str, rule_content: str) -> None:
        tool_rules = self.rules.setdefault(tool_name, {})
        bucket = tool_rules.setdefault(behavior, [])
        if rule_content not in bucket:
            bucket.append(rule_content)
        if self.store is not None:
            self.store.save({"rules": self.rules})

    def load_settings_rules(self, settings: dict, source: str) -> None:
        """Load rules from settings dict via rule hierarchy.

        If no hierarchy exists, this is a no-op.
        """
        if self.rule_hierarchy is not None:
            self.rule_hierarchy.load_from_settings(settings, source=source)
            # Re-sync rules from hierarchy
            effective_rules = self.rule_hierarchy.get_effective_rules()
            for tool_name, behaviors in effective_rules.items():
                tool_rules = self.rules.setdefault(tool_name, {})
                for behavior, rule_list in behaviors.items():
                    bucket = tool_rules.setdefault(behavior, [])
                    for rule in rule_list:
                        if rule not in bucket:
                            bucket.append(rule)

    def _extract_rule_target(self, tool_name: str, arguments: dict) -> str | None:
        if tool_name in {"run_shell", "run_powershell"}:
            command = arguments.get("command")
            return command if isinstance(command, str) else None
        if tool_name in {"Skill", "skill"}:
            skill = arguments.get("skill")
            if not isinstance(skill, str):
                return None
            arguments_text = arguments.get("arguments")
            if isinstance(arguments_text, str) and arguments_text.strip():
                return f"{skill} {arguments_text.strip()}"
            return skill
        for field_name in ("file_path", "path", "uri", "url"):
            value = arguments.get(field_name)
            if isinstance(value, str):
                return value
        return None

    def _match_rule(self, tool_name: str, behavior: str, target: str | None) -> str | None:
        if not target:
            return None
        for rule_content in self.rules.get(tool_name, {}).get(behavior, []):
            if match_permission_rule(parse_permission_rule(rule_content), target):
                return rule_content
        return None

    def _apply_mode_override(
        self, result: PermissionEvaluationResult
    ) -> PermissionEvaluationResult:
        """Apply mode-specific overrides to results (e.g., DONT_ASK mode)."""
        if result.requires_approval and self.mode == PermissionMode.DONT_ASK:
            self.denial_log.record(result.tool_name, "denied by dontAsk mode")
            return PermissionEvaluationResult.deny(
                tool_name=result.tool_name,
                reason="dontAsk mode: approval auto-denied",
                mode=self.mode,
            )
        return result

    async def _consult_ai_classifier(self, command: str) -> PermissionEvaluationResult | None:
        """Consult AI classifier for shell command evaluation.

        Returns None when the classifier is unavailable or errors, so callers
        fall back to the static classification result (typically an approval
        request) instead of treating classifier downtime as a denial.
        """
        if self._ai_classifier is None:
            return None

        try:
            from .ai_classifier import RiskLevel

            classification = await self._ai_classifier.classify(command)

            if classification.error:
                return None

            if not classification.allowed:
                self.denial_log.record("run_shell", f"AI classifier: {classification.reason}")
                return PermissionEvaluationResult.deny(
                    tool_name="run_shell",
                    reason=f"AI classifier denied: {classification.reason}",
                    mode=self.mode,
                )

            if classification.risk_level == RiskLevel.MODERATE:
                return PermissionEvaluationResult.approval_required(
                    tool_name="run_shell",
                    reason=f"AI classifier requires approval: {classification.reason}",
                    mode=self.mode,
                )

            # SAFE or allowed
            return PermissionEvaluationResult.allow(
                tool_name="run_shell",
                mode=self.mode,
                reason=f"AI classifier approved: {classification.reason}",
            )

        except Exception:
            # Classifier crashed: fall back to the static result.
            return None

    def _evaluate_file_tool(self, tool_name: str, arguments: dict) -> PermissionEvaluationResult:
        operation = {
            "read_file": "read",
            "write_file": "write",
            "edit_file": "write",
        }.get(tool_name)
        target = self._extract_rule_target(tool_name, arguments)
        if not operation or not target:
            return PermissionEvaluationResult.allow(
                tool_name=tool_name,
                mode=self.mode,
                reason="non-file tool allowed",
            )

        decision = evaluate_path_access(
            target,
            operation=operation,
            workspace_root=self.workspace_root,
            additional_roots=self.additional_roots,
        )
        if decision.requires_approval and self.mode == PermissionMode.ACCEPT_EDITS:
            from .modes import FILE_WRITE_TOOLS

            if tool_name in FILE_WRITE_TOOLS and decision.allowed:
                return PermissionEvaluationResult.allow(
                    tool_name=tool_name,
                    mode=self.mode,
                    reason="acceptEdits: workspace write auto-allowed",
                )
        if decision.requires_approval and self.mode != PermissionMode.BYPASS:
            return PermissionEvaluationResult.approval_required(
                tool_name=tool_name,
                reason=decision.reason,
                mode=self.mode,
            )
        if not decision.allowed:
            self.denial_log.record(tool_name, decision.reason)
            return PermissionEvaluationResult.deny(
                tool_name=tool_name,
                reason=decision.reason,
                mode=self.mode,
            )
        return PermissionEvaluationResult.allow(
            tool_name=tool_name,
            mode=self.mode,
            reason=decision.reason,
        )

    def evaluate_tool_call(self, tool_name: str, arguments: dict) -> PermissionEvaluationResult:
        target = self._extract_rule_target(tool_name, arguments)

        deny_rule = self._match_rule(tool_name, "deny", target)
        if deny_rule:
            self.denial_log.record(tool_name, f"Denied by rule: {deny_rule}")
            return PermissionEvaluationResult.deny(
                tool_name=tool_name,
                reason=f"Denied by rule: {deny_rule}",
                mode=self.mode,
                matched_rule=deny_rule,
            )

        allow_rule = self._match_rule(tool_name, "allow", target)
        if allow_rule:
            return PermissionEvaluationResult.allow(
                tool_name=tool_name,
                mode=self.mode,
                reason=f"Allowed by rule: {allow_rule}",
                matched_rule=allow_rule,
            )

        if self.mode == PermissionMode.PLAN:
            from .modes import READ_ONLY_TOOLS

            if tool_name in READ_ONLY_TOOLS:
                return PermissionEvaluationResult.allow(
                    tool_name=tool_name,
                    mode=self.mode,
                    reason="read-only tool allowed in plan mode",
                )
            return PermissionEvaluationResult.deny(
                tool_name=tool_name,
                reason="plan mode: mutations not allowed",
                mode=self.mode,
            )

        if self.mode == PermissionMode.BYPASS:
            return PermissionEvaluationResult.allow(
                tool_name=tool_name,
                mode=self.mode,
                reason="bypass mode",
            )

        if tool_name in {"run_shell", "run_powershell"}:
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                return PermissionEvaluationResult.allow(
                    tool_name=tool_name,
                    mode=self.mode,
                    reason="shell command validation deferred until invocation",
                )
            current_cwd = Path.cwd()
            state = resolve_sandbox_settings(current_cwd)
            excluded_from_sandbox = is_excluded_command(command, cwd=current_cwd)
            decision = (
                classify_powershell_command(command)
                if tool_name == "run_powershell"
                else classify_shell_command(command)
            )
            if state.enabled and not excluded_from_sandbox:
                if tool_name == "run_powershell":
                    reason = (
                        "sandbox is enabled, but PowerShell sandbox execution is not implemented; "
                        "add a sandbox exclusion with /sandbox exclude, or run /sandbox disable"
                    )
                    self.denial_log.record(tool_name, reason)
                    return PermissionEvaluationResult.deny(
                        tool_name=tool_name,
                        reason=reason,
                        mode=self.mode,
                    )
                if bool(arguments.get("run_in_background")):
                    reason = (
                        "sandbox is enabled, but background sandbox execution is not implemented "
                        "for model shell commands; run the command in the foreground, add a "
                        "sandbox exclusion with /sandbox exclude, or run /sandbox disable"
                    )
                    self.denial_log.record(tool_name, reason)
                    return PermissionEvaluationResult.deny(
                        tool_name=tool_name,
                        reason=reason,
                        mode=self.mode,
                    )

                if not state.backend_available or not state.platform_enabled:
                    reason = (
                        "sandbox is enabled, but the configured backend is unavailable; "
                        f"backend={state.backend}; reason={state.backend_reason}"
                    )
                    self.denial_log.record(tool_name, reason)
                    return PermissionEvaluationResult.deny(
                        tool_name=tool_name,
                        reason=reason,
                        mode=self.mode,
                    )
                elif tool_name == "run_shell" and state.policy is not None:
                    violation = read_only_violation(command, policy=state.policy)
                    if violation is None:
                        violation = protected_write_violation(
                            command,
                            policy=state.policy,
                            repo_root=current_cwd,
                        )
                    if violation is not None:
                        self.denial_log.record(tool_name, violation)
                        return PermissionEvaluationResult.deny(
                            tool_name=tool_name,
                            reason=violation,
                            mode=self.mode,
                        )
                    if (
                        decision.requires_approval
                        and state.auto_allow_bash_if_sandboxed
                        and state.backend_available
                    ):
                        return PermissionEvaluationResult.allow(
                            tool_name=tool_name,
                            mode=self.mode,
                            reason=(
                                f"sandboxed shell command auto-allowed; backend={state.backend}"
                            ),
                        )
            if not decision.allowed:
                self.denial_log.record(tool_name, decision.reason)
                return PermissionEvaluationResult.deny(
                    tool_name=tool_name,
                    reason=decision.reason,
                    mode=self.mode,
                )
            if decision.requires_approval:
                return self._apply_mode_override(
                    PermissionEvaluationResult.approval_required(
                        tool_name=tool_name,
                        reason=decision.reason,
                        mode=self.mode,
                    )
                )
            return self._apply_mode_override(
                PermissionEvaluationResult.allow(
                    tool_name=tool_name,
                    mode=self.mode,
                    reason=decision.reason,
                )
            )

        if tool_name in {"read_file", "write_file", "edit_file"}:
            return self._apply_mode_override(self._evaluate_file_tool(tool_name, arguments))

        return self._apply_mode_override(
            PermissionEvaluationResult.allow(
                tool_name=tool_name,
                mode=self.mode,
                reason="tool allowed by default",
            )
        )

    async def evaluate_tool_call_async(
        self, tool_name: str, arguments: dict
    ) -> PermissionEvaluationResult:
        """Async version of evaluate_tool_call that can consult AI classifier.

        For shell commands, if static classifier returns ambiguous (requires_approval),
        and AI classifier is available, consult it.
        """
        # First run static evaluation
        static_result = self.evaluate_tool_call(tool_name, arguments)

        # If it's a shell command that requires approval and AI classifier is available
        if (
            tool_name == "run_shell"
            and static_result.requires_approval
            and self._ai_classifier is not None
            and not static_result.matched_rule  # No explicit rule matched
        ):
            command = arguments.get("command", "")
            ai_result = await self._consult_ai_classifier(command)
            if ai_result is not None:
                return self._apply_mode_override(ai_result)

        return static_result
