import os

from settlesentry.agent.autonomous.graph import build_autonomous_graph
from settlesentry.agent.interface import Agent


def main() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required for autonomous smoke test.")

    agent = Agent(
        grouped_card_collection=True,
        graph=build_autonomous_graph(),
    )

    messages = [
        "Hi",
        "ACC1001",
        "Nithin Jain",
        "1990-05-14",
        "500",
        "Nithin Jain, 4532 0151 1283 0366, 12/2027, 123",
        "yes",
    ]

    for message in messages:
        print(f"\nUSER: {message}")

        response = agent.next(message)
        print(f"AGENT: {response['message']}")

        safe_state = agent.state.safe_view(session_id=agent.session_id)
        print(f"STATE: {safe_state.model_dump(mode='json')}")

        if agent.state.completed:
            break


if __name__ == "__main__":
    main()
