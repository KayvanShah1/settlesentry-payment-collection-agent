from __future__ import annotations

from dataclasses import dataclass

from settlesentry.agent.autonomous.graph import build_autonomous_graph
from settlesentry.agent.deps import AgentDeps


@dataclass
class FakeRuntime:
    message: str = "Hello, I’m SettleSentry. Please share your account ID."
    should_fail: bool = False

    def run_turn(self, deps: AgentDeps, user_input: str) -> str:
        if self.should_fail:
            raise RuntimeError("runtime failed")

        return self.message


def invoke_graph(deps: AgentDeps, runtime: FakeRuntime, user_input: str):
    graph = build_autonomous_graph(runtime=runtime)

    return graph.invoke(
        {
            "deps": deps,
            "user_input": user_input,
            "last_result": None,
            "final_response": "",
        }
    )


def test_autonomous_graph_persists_user_and_assistant_turns():
    deps = AgentDeps(grouped_card_collection=True)
    runtime = FakeRuntime(message="Hello, I’m SettleSentry. Please share your account ID.")

    result = invoke_graph(deps, runtime, "hi")

    assert result["final_response"] == "Hello, I’m SettleSentry. Please share your account ID."
    assert result["fallback_used"] is False
    assert result["safety_audit_status"] == "safe"

    assert len(deps.conversation_turns) == 2
    assert deps.conversation_turns[0].role == "user"
    assert deps.conversation_turns[0].content == "hi"
    assert deps.conversation_turns[1].role == "assistant"
    assert deps.conversation_turns[1].content == result["final_response"]


def test_autonomous_graph_uses_fallback_when_runtime_fails():
    deps = AgentDeps(grouped_card_collection=True)
    runtime = FakeRuntime(should_fail=True)

    result = invoke_graph(deps, runtime, "hi")

    assert result["fallback_used"] is True
    assert result["error_status"] == "autonomous_turn_failed"
    assert result["final_response"]

    assert len(deps.conversation_turns) == 2
    assert deps.conversation_turns[0].role == "user"
    assert deps.conversation_turns[0].content == "hi"
    assert deps.conversation_turns[1].role == "assistant"
    assert deps.conversation_turns[1].content == result["final_response"]


def test_autonomous_graph_uses_fallback_when_safety_audit_fails():
    deps = AgentDeps(grouped_card_collection=True)
    deps.state.provided_dob = "1990-05-14"

    runtime = FakeRuntime(message="Your DOB is 1990-05-14 and I can continue with the payment.")

    result = invoke_graph(deps, runtime, "status?")

    assert result["fallback_used"] is True
    assert result["error_status"] == "unsafe_message_dob_leak"
    assert result["safety_audit_status"] == "unsafe_message_dob_leak"
    assert "1990-05-14" not in result["final_response"]

    assert len(deps.conversation_turns) == 2
    assert deps.conversation_turns[0].role == "user"
    assert deps.conversation_turns[1].role == "assistant"


def test_autonomous_graph_does_not_call_real_runtime_when_fake_runtime_is_injected():
    deps = AgentDeps(grouped_card_collection=True)
    runtime = FakeRuntime(message="Please share your account ID.")

    result = invoke_graph(deps, runtime, "hello")

    assert result["final_response"] == "Please share your account ID."
    assert result["fallback_used"] is False
