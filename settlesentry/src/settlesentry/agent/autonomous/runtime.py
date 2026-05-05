from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from settlesentry.agent.autonomous.memory import build_autonomous_memory_payload
from settlesentry.agent.autonomous.prompts import AUTONOMOUS_AGENT_INSTRUCTIONS
from settlesentry.agent.autonomous.tools import available_toolsets
from settlesentry.agent.contracts import MessageResponse
from settlesentry.agent.deps import AgentDeps
from settlesentry.core import OperationLogContext, get_logger, settings

logger = get_logger("AutonomousAgentRuntime")


class AutonomousAgentRuntime:
    """PydanticAI runtime for the autonomous payment assistant."""

    def __init__(self) -> None:
        api_key = settings.llm.api_key.get_secret_value() if settings.llm.api_key else None

        if not api_key:
            raise RuntimeError("Autonomous agent requires OPENROUTER_API_KEY")

        self.agent = PydanticAgent(
            model=OpenRouterModel(
                model_name=settings.llm.model,
                provider=OpenRouterProvider(api_key=api_key),
                settings=OpenRouterModelSettings(
                    temperature=settings.llm.temperature,
                    max_tokens=settings.llm.max_tokens,
                    timeout=settings.llm.timeout_seconds,
                ),
            ),
            deps_type=AgentDeps,
            output_type=str,
            instructions=AUTONOMOUS_AGENT_INSTRUCTIONS,
            name="AutonomousPaymentAgent",
            retries=settings.llm.retries,
        )

    def run_turn(self, deps: AgentDeps, user_input: str) -> str:
        operation = OperationLogContext(operation="autonomous_llm_turn")
        payload = build_autonomous_memory_payload(deps, user_input)

        result = self.agent.run_sync(
            payload.model_dump_json(indent=2),
            deps=deps,
            toolsets=[available_toolsets(deps)],
        )

        message = str(result.output).strip()
        message = MessageResponse(message=message).message

        logger.info(
            "autonomous_llm_turn_completed",
            extra=operation.completed_extra(
                session_id=deps.session_id,
                step=deps.state.step.value,
                completed=deps.state.completed,
            ),
        )

        return message
