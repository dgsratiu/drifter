import os
import pytest

# Ensure DRIFTER_BIN is available to all tests
def pytest_configure(config):
    # Set DRIFTER_BIN if not already set
    if "DRIFTER_BIN" not in os.environ:
        os.environ["DRIFTER_BIN"] = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "rust", "target", "debug", "drifter"
        ))