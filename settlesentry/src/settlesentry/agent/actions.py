from enum import StrEnum, auto


class UserIntent(StrEnum):
    UNKNOWN = auto()

    LOOKUP_ACCOUNT = auto()
    VERIFY_IDENTITY = auto()
    MAKE_PAYMENT = auto()
    CONFIRM_PAYMENT = auto()
    CANCEL = auto()

    ASK_AGENT_IDENTITY = auto()
    ASK_AGENT_CAPABILITY = auto()
    ASK_CURRENT_STATUS = auto()
    ASK_TO_REPEAT = auto()
    CORRECT_PREVIOUS_DETAIL = auto()


class ProposedAction(StrEnum):
    """
    Action suggested by the LLM/regex parser.

    This is not directly executed. The policy layer still decides whether the
    action is allowed.
    """

    NONE = auto()
    LOOKUP_ACCOUNT = auto()
    VERIFY_IDENTITY = auto()
    PREPARE_PAYMENT = auto()
    CONFIRM_PAYMENT = auto()
    PROCESS_PAYMENT = auto()
    HANDLE_CORRECTION = auto()
    CANCEL = auto()


class AgentAction(StrEnum):
    """
    Actual action chosen by the deterministic agent controller.
    """

    ASK_ACCOUNT_ID = auto()
    LOOKUP_ACCOUNT = auto()

    ASK_FULL_NAME = auto()
    ASK_SECONDARY_FACTOR = auto()
    VERIFY_IDENTITY = auto()

    SHARE_BALANCE = auto()

    ASK_PAYMENT_AMOUNT = auto()
    ASK_CARD_DETAILS = auto()
    ASK_CVV = auto()

    ASK_PAYMENT_CONFIRMATION = auto()
    PROCESS_PAYMENT = auto()

    HANDLE_SIDE_QUESTION = auto()
    HANDLE_CORRECTION = auto()
    HANDLE_LOOKUP_FAILURE = auto()
    HANDLE_PAYMENT_FAILURE = auto()
    CLOSE = auto()
