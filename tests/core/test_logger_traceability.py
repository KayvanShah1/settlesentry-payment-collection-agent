import logging

from settlesentry.core.logger import ContextAwareFormatter, SensitiveDataFilter
from settlesentry.security.redaction import MASK


def test_context_formatter_appends_extra_fields():
    record = logging.LogRecord(
        name="PaymentsClient",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="lookup_account_started",
        args=(),
        exc_info=None,
    )
    record.operation_id = "op_abc123"
    record.account_id = "ACC1001"
    record.tool_name = "lookup_account"

    SensitiveDataFilter().filter(record)
    rendered = ContextAwareFormatter("%(name)s - %(message)s").format(record)

    assert "lookup_account_started" in rendered
    assert "operation_id=op_abc123" in rendered
    assert f"account_id={MASK}" in rendered
    assert "tool_name=lookup_account" in rendered


def test_context_formatter_shows_redacted_extra_values():
    record = logging.LogRecord(
        name="PaymentsClient",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="process_payment_started",
        args=(),
        exc_info=None,
    )
    record.operation_id = "op_sensitive"
    record.card_number = "4532015112830366"

    SensitiveDataFilter().filter(record)
    rendered = ContextAwareFormatter("%(name)s - %(message)s").format(record)

    assert "operation_id=op_sensitive" in rendered
    assert f"card_number={MASK}" in rendered
