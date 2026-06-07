"""Developable package — data carrier for CLI skills and templates."""
from pathlib import Path

__version__ = "0.1.0"

# Path to this package's data directory (templates, skills, commands)
DATA_DIR = Path(__file__).parent
