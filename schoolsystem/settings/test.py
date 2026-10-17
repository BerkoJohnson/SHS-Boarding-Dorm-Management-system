import os

os.environ["TENANCY_ENABLED"] = "false"

from .development import *


DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_schoolsystem_core"
