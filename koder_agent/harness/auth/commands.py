"""Harness auth subcommand flows."""

from __future__ import annotations

import asyncio
import json
import numbers
import os
import time
from datetime import datetime
from typing import Dict, Optional

from rich.console import Console
from rich.panel import Panel

from koder_agent.auth.callback_server import CallbackResult, run_oauth_flow
from koder_agent.auth.constants import SUPPORTED_PROVIDERS, TOKEN_EXPIRY_BUFFER_MS
from koder_agent.auth.providers import get_provider
from koder_agent.auth.token_storage import get_token_storage

console = Console()

PROVIDER_DESCRIPTIONS: Dict[str, str] = {
    "google": "Gemini CLI (free with Google account)",
    "claude": "Claude Max subscription",
    "chatgpt": "ChatGPT Plus/Pro subscription",
    "antigravity": "Antigravity (Gemini 3 + Claude models)",
}

GITHUB_COPILOT_PROVIDER_ID = "github_copilot"
GITHUB_COPILOT_PROVIDER_ALIASES = {
    GITHUB_COPILOT_PROVIDER_ID,
    "github-copilot",
    "copilot",
}


def _normalize_provider_id(provider_id: str) -> str:
    provider = provider_id.strip().lower()
    if provider in GITHUB_COPILOT_PROVIDER_ALIASES:
        return GITHUB_COPILOT_PROVIDER_ID
    return provider


async def handle_login(provider_id: str, timeout: float = 300) -> bool:
    provider_id = _normalize_provider_id(provider_id)
    if provider_id == GITHUB_COPILOT_PROVIDER_ID:
        return await handle_github_copilot_login(timeout=timeout)

    if provider_id not in SUPPORTED_PROVIDERS:
        console.print(
            f"[red]Error:[/red] Unknown provider '{provider_id}'. "
            f"Supported: {', '.join([*SUPPORTED_PROVIDERS, GITHUB_COPILOT_PROVIDER_ID])}"
        )
        return False

    console.print(f"\n[bold]Authenticating with {provider_id}...[/bold]\n")
    try:
        provider = get_provider(provider_id)
        auth_url, verifier = provider.get_authorization_url()

        if provider_id == "claude":
            result = await _handle_manual_code_flow(auth_url, timeout)
        else:
            result = await run_oauth_flow(
                auth_url,
                port=provider.callback_port,
                callback_path=provider.callback_path,
                timeout=timeout,
            )

        if not result.success:
            console.print(
                f"[red]Authentication failed:[/red] {result.error}\n"
                f"{result.error_description or ''}"
            )
            return False

        console.print("Exchanging authorization code for tokens...")
        exchange_result = await provider.exchange_code(result.code, verifier)
        if not exchange_result.success:
            console.print(f"[red]Token exchange failed:[/red] {exchange_result.error}")
            return False

        tokens = exchange_result.tokens
        console.print("Fetching available models...")
        models, _ = await provider.list_models(tokens.access_token)
        tokens.models = models
        tokens.models_fetched_at = int(time.time() * 1000)

        storage = get_token_storage()
        storage.save(tokens)

        email = tokens.email or "Unknown"
        description = PROVIDER_DESCRIPTIONS.get(provider_id, "")
        success_msg = (
            f"[green]Successfully authenticated![/green]\n\n"
            f"Provider: {provider_id} ({description})\n"
            f"Account: {email}\n"
        )
        if models:
            success_msg += f"\n[bold]Available Models ({len(models)}):[/bold]\n"
            display_models = models[:5]
            success_msg += ", ".join(display_models)
            if len(models) > 5:
                success_msg += f"\n  +{len(models) - 5} more"
        else:
            success_msg += "\n[dim]No models found[/dim]"

        console.print(Panel(success_msg, title="Authentication Complete", border_style="green"))
        return True
    except Exception as exc:
        console.print(f"[red]Error during authentication:[/red] {exc}")
        return False


async def handle_github_copilot_login(timeout: float = 300) -> bool:
    """Force GitHub Copilot device-flow login through LiteLLM's authenticator."""
    _ = timeout

    console.print("\n[bold]Authenticating with github_copilot...[/bold]\n")
    try:
        from litellm.llms.github_copilot.authenticator import Authenticator

        def _login_and_refresh() -> tuple[str, dict]:
            authenticator = Authenticator()
            access_token = authenticator._login()
            with open(authenticator.access_token_file, "w", encoding="utf-8") as file:
                file.write(access_token)
            api_key_info = authenticator._refresh_api_key()
            with open(authenticator.api_key_file, "w", encoding="utf-8") as file:
                json.dump(api_key_info, file)
            return authenticator.token_dir, api_key_info

        token_dir, api_key_info = await asyncio.to_thread(_login_and_refresh)
        endpoints = api_key_info.get("endpoints") or {}
        api_endpoint = endpoints.get("api") or "default"
        console.print(
            Panel(
                "[green]Successfully authenticated![/green]\n\n"
                "Provider: github_copilot\n"
                f"Token cache: {token_dir}\n"
                f"API endpoint: {api_endpoint}",
                title="Authentication Complete",
                border_style="green",
            )
        )
        return True
    except Exception as exc:
        console.print(f"[red]GitHub Copilot authentication failed:[/red] {exc}")
        return False


async def _handle_manual_code_flow(auth_url: str, timeout: float) -> CallbackResult:
    import webbrowser

    console.print("Opening browser for authentication...")
    console.print(f"\n[dim]URL: {auth_url}[/dim]\n")
    webbrowser.open(auth_url)
    console.print(
        "[yellow]After authorizing, you'll see a code on the page.[/yellow]\n"
        "[yellow]Copy the entire code and paste it below.[/yellow]\n"
    )
    try:
        code = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: input("Paste the authorization code: ")
            ),
            timeout=timeout,
        )
        if not code or not code.strip():
            return CallbackResult(
                success=False,
                error="empty_code",
                error_description="No authorization code provided",
            )
        return CallbackResult(success=True, code=code.strip())
    except asyncio.TimeoutError:
        return CallbackResult(
            success=False,
            error="timeout",
            error_description=f"No code entered within {timeout} seconds",
        )


async def handle_list() -> None:
    storage = get_token_storage()
    all_tokens = storage.get_all_tokens()
    if not all_tokens:
        console.print(
            "\n[yellow]No OAuth providers configured.[/yellow]\n"
            "Use 'koder auth login <provider>' to authenticate.\n"
            f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}"
        )
        return

    for provider_id, tokens in all_tokens.items():
        description = PROVIDER_DESCRIPTIONS.get(provider_id, "")
        account = tokens.email if tokens.email else None
        info = (
            f"[bold]Account:[/bold] {account}\n[bold]Type:[/bold] {description}\n"
            if account
            else f"[bold]Type:[/bold] {description}\n"
        )
        access_token = tokens.access_token
        if tokens.is_expired():
            try:
                provider = get_provider(provider_id)
                result = await provider.refresh_tokens(tokens.refresh_token)
                if result.success and result.tokens:
                    storage.save(result.tokens)
                    tokens = result.tokens
                    access_token = tokens.access_token
            except Exception as exc:
                info += f"\n[red]Token refresh failed: {exc}[/red]\n"

        models = tokens.models
        source = "cached"
        if not tokens.is_models_cache_valid() or not models:
            try:
                provider = get_provider(provider_id)
                models, status = await provider.list_models(access_token)
                source = status.get("source", "api")
                tokens.models = models
                tokens.models_fetched_at = int(time.time() * 1000)
                storage.save(tokens)
            except Exception:
                source = "cached" if models else "unavailable"

        if models:
            source_label = "[green]API[/green]" if source == "api" else "[cyan]cached[/cyan]"
            info += f"\n[bold]Models ({len(models)}):[/bold] {source_label}\n"
            for model in sorted(models):
                info += f"  • {model}\n"
        else:
            info += "\n[dim]No models available[/dim]\n"

        console.print(
            Panel(info.strip(), title=f"[bold cyan]{provider_id}[/bold cyan]", border_style="blue")
        )
        console.print()


async def handle_revoke(provider_id: str) -> bool:
    provider_id = _normalize_provider_id(provider_id)
    if provider_id == GITHUB_COPILOT_PROVIDER_ID:
        console.print(
            "[yellow]GitHub Copilot tokens are managed by LiteLLM. "
            "Run `koder auth login github_copilot` to refresh the login.[/yellow]"
        )
        return False

    storage = get_token_storage()
    tokens = storage.load(provider_id)
    if not tokens:
        console.print(f"[yellow]No tokens found for provider '{provider_id}'[/yellow]")
        return False

    try:
        provider = get_provider(provider_id)
        await provider.revoke_token(tokens.refresh_token)
    except Exception:
        pass

    storage.delete(provider_id)
    console.print(f"[green]Tokens revoked for {provider_id}[/green]")
    return True


async def handle_status(provider_id: Optional[str] = None) -> None:
    if provider_id:
        provider_id = _normalize_provider_id(provider_id)
    if provider_id == GITHUB_COPILOT_PROVIDER_ID:
        await _print_github_copilot_status()
        return

    storage = get_token_storage()
    if provider_id:
        tokens = storage.load(provider_id)
        if not tokens:
            console.print(f"[yellow]No tokens found for provider '{provider_id}'[/yellow]")
            return
        await _print_token_details(provider_id, tokens, storage)
        return

    all_tokens = storage.get_all_tokens()
    if not all_tokens:
        console.print("[yellow]No OAuth providers configured.[/yellow]")
        return

    for pid, tokens in all_tokens.items():
        await _print_token_details(pid, tokens, storage)
        console.print()


async def _print_token_details(provider_id: str, tokens, storage) -> None:
    access_token = tokens.access_token
    if tokens.is_expired():
        try:
            provider = get_provider(provider_id)
            result = await provider.refresh_tokens(tokens.refresh_token)
            if result.success and result.tokens:
                storage.save(result.tokens)
                tokens = result.tokens
                access_token = tokens.access_token
        except Exception:
            pass

    if tokens.is_expired(0):
        status = "[red]EXPIRED[/red]"
    elif tokens.is_expired(TOKEN_EXPIRY_BUFFER_MS):
        status = "[yellow]EXPIRING SOON[/yellow]"
    else:
        status = "[green]VALID[/green]"

    now_ms = int(datetime.now().timestamp() * 1000)
    time_left_ms = tokens.expires_at - now_ms
    time_left_mins = max(0, time_left_ms // 60000)
    expires = datetime.fromtimestamp(tokens.expires_at / 1000)

    account_line = f"Account: {tokens.email}\n" if tokens.email else ""
    info = (
        f"Status: {status}\n"
        f"{account_line}"
        f"Expires: {expires.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Time left: {time_left_mins} minutes\n"
        f"Access token: {tokens.access_token[:20]}...\n"
        f"Refresh token: {tokens.refresh_token[:20]}..."
    )

    models = tokens.models
    source = "cached"
    if not tokens.is_models_cache_valid() or not models:
        try:
            provider = get_provider(provider_id)
            models, status_info = await provider.list_models(access_token)
            source = "api" if status_info.get("source") == "api" else "cached"
            tokens.models = models
            tokens.models_fetched_at = int(time.time() * 1000)
            storage.save(tokens)
        except Exception:
            pass

    if models:
        source_label = "[green]API[/green]" if source == "api" else "[cyan]cached[/cyan]"
        info += f"\n\n[bold]Models ({len(models)}):[/bold] {source_label}\n"
        for model in sorted(models):
            info += f"  • {model}\n"

    console.print(Panel(info.strip(), title=f"[bold]{provider_id}[/bold]", border_style="blue"))


async def _print_github_copilot_status() -> None:
    try:
        from litellm.llms.github_copilot.authenticator import Authenticator

        authenticator = Authenticator()
        access_token_exists = bool(
            authenticator.access_token_file and os.path.exists(authenticator.access_token_file)
        )
        api_key_info = None
        try:
            with open(authenticator.api_key_file, encoding="utf-8") as file:
                api_key_info = json.load(file)
        except Exception:
            pass

        raw_expires_at = api_key_info.get("expires_at") if isinstance(api_key_info, dict) else None
        expires_at = raw_expires_at if isinstance(raw_expires_at, numbers.Real) else None
        status = "[yellow]NEEDS LOGIN[/yellow]"
        expires_line = "Expires: unavailable"
        if expires_at:
            expires = datetime.fromtimestamp(expires_at)
            expires_line = f"Expires: {expires.strftime('%Y-%m-%d %H:%M:%S')}"
            status = "[green]VALID[/green]" if expires_at > time.time() else "[red]EXPIRED[/red]"

        info = (
            f"Status: {status}\n"
            f"Access token: {'present' if access_token_exists else 'missing'}\n"
            f"{expires_line}\n"
            f"Token cache: {authenticator.token_dir}\n\n"
            "Refresh login: koder auth login github_copilot"
        )
        console.print(Panel(info, title="[bold]github_copilot[/bold]", border_style="blue"))
    except Exception as exc:
        console.print(f"[red]Unable to inspect GitHub Copilot status:[/red] {exc}")


def show_auth_help() -> None:
    help_text = """[bold]OAuth Authentication Commands[/bold]

Commands:
  login <provider>    Authenticate with a provider
  list                List configured OAuth providers and models
  revoke <provider>   Revoke OAuth tokens
  status [provider]   Show OAuth token status

Providers:
  google, claude, chatgpt, antigravity, github_copilot
"""
    console.print(Panel(help_text, title="koder auth", border_style="blue"))


async def handle_auth_subcommand(args) -> int:
    if args.auth_command == "login":
        success = await handle_login(args.provider, timeout=args.timeout)
        return 0 if success else 1
    if args.auth_command == "list":
        await handle_list()
        return 0
    if args.auth_command == "revoke":
        success = await handle_revoke(args.provider)
        return 0 if success else 1
    if args.auth_command == "status":
        await handle_status(getattr(args, "provider", None))
        return 0
    show_auth_help()
    return 0
