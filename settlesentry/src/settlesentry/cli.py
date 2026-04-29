from __future__ import annotations

import os
from enum import StrEnum
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    help="SettleSentry payment collection agent CLI.",
    no_args_is_help=False,
    invoke_without_command=True,
)

console = Console()


class AgentMode(StrEnum):
    LOCAL = "local"
    LLM = "llm"
    FULL_LLM = "full-llm"


def configure_console_logging(debug_logs: bool) -> None:
    """
    Configure console logging before importing agent modules.
    """
    os.environ["LOG_CONSOLE_ENABLED"] = "true" if debug_logs else "false"


def build_agent(mode: AgentMode):
    """
    Build an Agent lazily after logging env has been configured.

    Modes:
    - local: deterministic parser + deterministic responder
    - llm: LLM parser + deterministic responder
    - full-llm: LLM parser + LLM responder
    """
    from settlesentry.agent.agent import Agent
    from settlesentry.agent.parser import build_input_parser
    from settlesentry.agent.parsers.deterministic import DeterministicInputParser
    from settlesentry.agent.responder import DeterministicResponseGenerator, build_response_generator
    from settlesentry.core import settings

    if mode == AgentMode.LOCAL:
        return Agent(
            parser=DeterministicInputParser(),
            responder=DeterministicResponseGenerator(),
            grouped_card_collection=False,
        )

    if not settings.llm.api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. Use --mode local or set OPENROUTER_API_KEY in your environment/.env file."
        )

    if mode == AgentMode.LLM:
        return Agent(
            parser=build_input_parser(),
            responder=DeterministicResponseGenerator(),
            grouped_card_collection=True,
        )

    return Agent(
        parser=build_input_parser(),
        responder=build_response_generator(),
        grouped_card_collection=True,
    )


def validate_agent_response(response: dict) -> str:
    """
    Validate the required assignment response contract.
    """
    if not isinstance(response, dict):
        raise ValueError(f"Agent.next() returned {type(response).__name__}; expected dict.")

    if set(response.keys()) != {"message"}:
        raise ValueError(f"Agent.next() returned keys {sorted(response.keys())}; expected ['message'].")

    message = response.get("message")

    if not isinstance(message, str) or not message.strip():
        raise ValueError("Agent.next() must return {'message': non-empty str}.")

    return message


def print_header(mode: AgentMode, debug_logs: bool) -> None:
    descriptions = {
        AgentMode.LOCAL: "Local mode: deterministic parser and deterministic responses.",
        AgentMode.LLM: "LLM mode: LLM parser with deterministic responses.",
        AgentMode.FULL_LLM: "Full LLM mode: LLM parser and LLM-written responses.",
    }

    logging_text = "console logs enabled" if debug_logs else "console logs disabled"

    console.print(
        Panel.fit(
            f"[bold]SettleSentry Payment Collection Agent[/bold]\n{descriptions[mode]}\n[dim]{logging_text}[/dim]",
            border_style="blue",
        )
    )


def run_chat(
    *,
    mode: AgentMode,
    show_state: bool,
    debug_logs: bool,
) -> None:
    configure_console_logging(debug_logs)

    try:
        agent = build_agent(mode)
    except Exception as exc:
        console.print(f"[red]Could not start agent:[/red] {exc}")
        raise typer.Exit(1) from exc

    print_header(mode, debug_logs)
    console.print("[dim]Type 'exit' or 'quit' to stop.[/dim]\n")

    while True:
        user_input = typer.prompt("USER").strip()

        if user_input.lower() in {"exit", "quit"}:
            console.print("[yellow]Exiting.[/yellow]")
            return

        try:
            response = agent.next(user_input)
            message = validate_agent_response(response)
        except Exception as exc:
            console.print(f"[red]Agent error:[/red] {type(exc).__name__}: {exc}")
            raise typer.Exit(1) from exc

        console.print(f"\n[bold green]AGENT:[/bold green] {message}\n")

        if show_state:
            safe_state = agent.state.safe_view(session_id=agent.session_id)
            console.print("[dim]Privacy-safe state:[/dim]")
            console.print_json(safe_state.model_dump_json())

        if agent.state.completed:
            console.print("[bold blue]Conversation completed.[/bold blue]")
            return


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    mode: Annotated[
        AgentMode,
        typer.Option(
            "--mode",
            "-m",
            help="Agent mode: local, llm, or full-llm.",
        ),
    ] = AgentMode.LLM,
    show_state: Annotated[
        bool,
        typer.Option(
            "--show-state",
            help="Print privacy-safe state after each turn.",
        ),
    ] = False,
    debug_logs: Annotated[
        bool,
        typer.Option(
            "--debug-logs",
            help="Show internal application logs in the console.",
        ),
    ] = False,
) -> None:
    """
    Run interactive chat by default when no subcommand is provided.
    """
    if ctx.invoked_subcommand is None:
        run_chat(
            mode=mode,
            show_state=show_state,
            debug_logs=debug_logs,
        )


@app.command()
def chat(
    mode: Annotated[
        AgentMode,
        typer.Option(
            "--mode",
            "-m",
            help="Agent mode: local, llm, or full-llm.",
        ),
    ] = AgentMode.LLM,
    show_state: Annotated[
        bool,
        typer.Option(
            "--show-state",
            help="Print privacy-safe state after each turn.",
        ),
    ] = False,
    debug_logs: Annotated[
        bool,
        typer.Option(
            "--debug-logs",
            help="Show internal application logs in the console.",
        ),
    ] = False,
) -> None:
    """
    Run the payment collection agent interactively.
    """
    run_chat(
        mode=mode,
        show_state=show_state,
        debug_logs=debug_logs,
    )


if __name__ == "__main__":
    app()
