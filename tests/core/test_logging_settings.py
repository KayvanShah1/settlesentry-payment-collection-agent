from settlesentry.core import settings


def test_test_environment_uses_separate_log_filename():
    assert settings.logging.file_name == "settlesentry.test.log"
