"""Make the package modules importable from tests without an install step."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
