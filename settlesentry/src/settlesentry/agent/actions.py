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
