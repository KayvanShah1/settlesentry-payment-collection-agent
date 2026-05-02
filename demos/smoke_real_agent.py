from __future__ import annotations

import argparse
import os
from typing import Literal

from settlesentry.agent.interface import Agent
from settlesentry.agent.parsing.deterministic import DeterministicInputParser
from settlesentry.agent.response.writer import DeterministicResponseGenerator

AgentMode = Literal["deterministic", "llm"]


DETERMINISTIC_HAPPY_PATH_MESSAGES = [
    "Hi",
    "My account ID is ACC1001",
    "Nithin Jain",
    "DOB is 1990-05-14",
    "I want to pay 500.00",
    "Nithin Jain",
    "4532 0151 1283 0366",
    "12/2027",
    "123",
    "yes",
]


LLM_HAPPY_PATH_MESSAGES = [
    "Hi",
    "My account ID is ACC1001",
    "Nithin Jain",
    "DOB is 1990-05-14",
    "I want to pay 500.00",
    "cardholder Nithin Jain, card number 4532 0151 1283 0366, expiry 12/2027",
    "123",
    "yes",
]


def build_agent(agent_mode: AgentMode) -> Agent:
    if agent_mode == "deterministic":
        return Agent(
            parser=DeterministicInputParser(),
            responder=DeterministicResponseGenerator(),
            grouped_card_collection=False,
        )

    validate_llm_environment()

    return Agent(
        grouped_card_collection=True,
    )


def happy_path_messages(agent_mode: AgentMode) -> list[str]:
    if agent_mode == "llm":
        return LLM_HAPPY_PATH_MESSAGES

    return DETERMINISTIC_HAPPY_PATH_MESSAGES


def print_turn(role: str, message: str) -> None:
    print(f"\n{role}:")
    print(message)


def response_message(response: dict[str, str]) -> str:
    """
    Validate the assignment response shape and return the user-facing message.
    """
    if not isinstance(response, dict):
        raise TypeError(f"Agent.next() must return dict, got {type(response).__name__}")

    message = response.get("message")

    if not isinstance(message, str):
        raise TypeError("Agent.next() must return {'message': str}")

    return message


def print_safe_state(agent: Agent) -> None:
    print("\nFinal safe state:")
    print(agent.state.safe_view(session_id=agent.session_id).model_dump_json(indent=2))


def print_run_header(agent_mode: AgentMode, mode: str) -> None:
    print(f"\nRunning SettleSentry smoke test: mode={mode}, agent_mode={agent_mode}")

    if agent_mode == "llm":
        print("Using configured OpenRouter parser/responder and grouped card collection.")
    else:
        print("Using deterministic parser/responder and sequential card collection.")

    print("Payment lookup/payment calls still use the configured assignment payment API.")


def run_happy_path(agent_mode: AgentMode) -> None:
    agent = build_agent(agent_mode)

    print_run_header(agent_mode=agent_mode, mode="happy-path")

    for user_message in happy_path_messages(agent_mode):
        print_turn("USER", user_message)

        response = agent.next(user_message)
        message = response_message(response)

        print_turn("AGENT", message)

        if agent.state.completed:
            break

    print_safe_state(agent)


def run_interactive(agent_mode: AgentMode) -> None:
    agent = build_agent(agent_mode)

    print_run_header(agent_mode=agent_mode, mode="interactive")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        user_message = input("USER: ").strip()

        if user_message.lower() in {"exit", "quit"}:
            break

        response = agent.next(user_message)
        message = response_message(response)

        print(f"AGENT: {message}\n")

        if agent.state.completed:
            print("Conversation completed.")
            print_safe_state(agent)
            break


def validate_llm_environment() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError(
            "OPENROUTER_API_KEY is missing. "
            "Use --agent-mode deterministic or add OPENROUTER_API_KEY to your environment/.env file."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a SettleSentry agent smoke test.")

    parser.add_argument(
        "--mode",
        choices=("happy-path", "interactive"),
        default="happy-path",
        help="Smoke test mode to run.",
    )

    parser.add_argument(
        "--agent-mode",
        choices=("deterministic", "llm"),
        default="llm",
        help=(
            "deterministic = fast local parser/responder with sequential card collection. "
            "llm = OpenRouter parser/responder with grouped card collection."
        ),
    )

    args = parser.parse_args()

    if args.mode == "interactive":
        run_interactive(agent_mode=args.agent_mode)
    else:
        run_happy_path(agent_mode=args.agent_mode)


if __name__ == "__main__":
    main()
