from __future__ import annotations

import argparse
import os
from datetime import date

from settlesentry.agent.agent import Agent

HAPPY_PATH_MESSAGES = [
    "Hi",
    "My account ID is ACC1001",
    "Nithin Jain",
    "DOB is 1990-05-14",
    "I want to pay 500.00",
    "Nithin Jain",
    "4532 0151 1283 0366",
    "123",
    f"12/{date.today().year + 2}",
    "yes",
]


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


def run_happy_path() -> None:
    agent = Agent()

    print("\nRunning real-agent happy-path smoke test.")
    print("This uses the configured OpenRouter model and the live assignment payment API.")

    for user_message in HAPPY_PATH_MESSAGES:
        print_turn("USER", user_message)

        response = agent.next(user_message)
        message = response_message(response)

        print_turn("AGENT", message)

        if agent.state.completed:
            break

    print_safe_state(agent)


def run_interactive() -> None:
    agent = Agent()

    print("\nInteractive SettleSentry smoke test.")
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


def validate_environment() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is missing. Add it to your environment or .env file.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real SettleSentry agent smoke test.")
    parser.add_argument(
        "--mode",
        choices=("happy-path", "interactive"),
        default="interactive",
        help="Smoke test mode to run.",
    )

    args = parser.parse_args()

    validate_environment()

    if args.mode == "interactive":
        run_interactive()
    else:
        run_happy_path()


if __name__ == "__main__":
    main()
