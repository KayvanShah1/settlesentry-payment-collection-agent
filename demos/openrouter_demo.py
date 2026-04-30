from __future__ import annotations

import argparse

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from settlesentry.agent.parsing.base import ParserContext
from settlesentry.agent.parsing.llm import PydanticAIInputParser
from settlesentry.agent.state import ConversationState, ConversationStep
from settlesentry.core import settings

console = Console()

DEMO_CASES = [
    {
        "name": "comprehensive-input",
        "message": (
            "Hi, my account id is ACC1001. "
            "My name is Nithin Jain, dob is 1990-05-14, aadhaar last 4 is 4321, pincode 400001. "
            "I want to pay 500.50. Cardholder name is Nithin Jain, card number is 4532 0151 1283 0366, "
            "cvv is 123, expiry is 12/2027. Please proceed."
        ),
        "step": ConversationStep.START,
        "expected_fields": (),
        "assistant_question": None,
    },
    {
        "name": "missing-fields-input",
        "message": (
            "Account ACC1001. Name Nithin Jain. dob 1990-05-14. "
            "I want to pay 1,237.78 using card 4532 0151 1283 0366 expiry 12/2027."
        ),
        "step": ConversationStep.START,
        "expected_fields": (),
        "assistant_question": None,
    },
    {
        "name": "step-reply-dob",
        "message": "1990-05-14",
        "step": ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        "expected_fields": ("dob",),
        "assistant_question": "What is your DOB (YYYY-MM-DD)?",
    },
    {
        "name": "step-reply-cvv",
        "message": "123",
        "step": ConversationStep.WAITING_FOR_CVV,
        "expected_fields": ("cvv",),
        "assistant_question": "Please provide CVV.",
    },
    {
        "name": "form-reply-identity",
        "message": "Nithin Jain, 1990-05-14, 400001",
        "step": ConversationStep.WAITING_FOR_SECONDARY_FACTOR,
        "expected_fields": ("full_name", "dob", "pincode"),
        "assistant_question": "Please share full name, DOB, and pincode in order.",
    },
    {
        "name": "form-reply-card",
        "message": "Nithin Jain, 4532 0151 1283 0366, 123, 12/2027",
        "step": ConversationStep.WAITING_FOR_CARDHOLDER_NAME,
        "expected_fields": ("cardholder_name", "card_number", "cvv", "expiry"),
        "assistant_question": "Please share cardholder name, card number, CVV, and expiry in order.",
    },
]


def has_api_key() -> bool:
    return bool(settings.llm.api_key and settings.llm.api_key.get_secret_value())


def unique_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for model in models:
        if model and model not in seen:
            ordered.append(model)
            seen.add(model)
    return ordered


def default_free_models() -> list[str]:
    models = ["openrouter/free", "nvidia/nemotron-3-super-120b-a12b:free"]
    if ":free" in settings.llm.model:
        models.insert(0, settings.llm.model)
    return unique_models(models)


def run_parse_with_model(
    *,
    model: str,
    message: str,
    step: ConversationStep = ConversationStep.START,
    expected_fields: tuple[str, ...] = (),
    assistant_question: str | None = None,
) -> dict:
    original_enabled = settings.llm.enabled
    original_model = settings.llm.model
    try:
        settings.llm.enabled = True
        settings.llm.model = model

        parser = PydanticAIInputParser()
        context = ParserContext.from_state(
            ConversationState(step=step),
            expected_fields=expected_fields,
            last_assistant_message=assistant_question,
        )
        output = parser.extract(message, context=context)
        return {
            "ok": True,
            "step": step.value,
            "expected_fields": list(expected_fields),
            "assistant_question": assistant_question,
            "output": output.model_dump(mode="json"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "step": step.value,
            "expected_fields": list(expected_fields),
            "assistant_question": assistant_question,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        settings.llm.enabled = original_enabled
        settings.llm.model = original_model


def print_case(*, model: str, case_name: str, message: str, result: dict) -> None:
    console.rule(f"[bold cyan]{model}[/bold cyan] :: {case_name}")
    console.print_json(
        data={
            "step": result.get("step"),
            "expected_fields": result.get("expected_fields"),
            "assistant_question": result.get("assistant_question"),
        }
    )
    console.print(Panel(message, title="Input Message", expand=False))

    if result["ok"]:
        console.print(Text("Parsed JSON", style="bold green"))
        console.print_json(data=result["output"])
    else:
        console.print(Text("Parser Error", style="bold red"))
        console.print_json(
            data={
                "error_type": result["error_type"],
                "error": result["error"],
            }
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run free-model OpenRouter parser demo with full and missing-field inputs.",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        default=[
            # "openrouter/free",
            # "openai/gpt-oss-120b:free",
            "openai/gpt-oss-20b:free",
        ],
        help="Free models to test. Defaults to configured free model (if any), openrouter/free, and Nemotron free.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    console.print_json(
        data={
            "configured_model": settings.llm.model,
            "llm_enabled": settings.llm.enabled,
            "has_api_key": has_api_key(),
        }
    )

    if not has_api_key():
        console.print("[bold red]OPENROUTER_API_KEY missing.[/bold red]")
        return

    models = unique_models(args.models) if args.models else default_free_models()

    for model in models:
        for case in DEMO_CASES:
            result = run_parse_with_model(
                model=model,
                message=case["message"],
                step=case["step"],
                expected_fields=case["expected_fields"],
                assistant_question=case["assistant_question"],
            )
            print_case(
                model=model,
                case_name=case["name"],
                message=case["message"],
                result=result,
            )


if __name__ == "__main__":
    main()
