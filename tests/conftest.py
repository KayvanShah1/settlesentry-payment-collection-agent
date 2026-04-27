import os

# Keep test logs isolated from local runtime/demo logs.
os.environ.setdefault("LOG_FILE_NAME", "settlesentry.test.log")
