import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USER = os.getenv("GITHUB_USER", "")
MODEL = "claude-sonnet-4-6"
MODEL_FAST = "claude-haiku-4-5-20251001"
TEMPERATURE_LLM = 0.2

# Per-task output token limits (generous but not wasteful)
TASK_MAX_TOKENS: dict[str, int] = {
    "validation_logic": 800,
    "seed_data": 600,
    "entity_write_validation": 700,
}
TASK_MAX_TOKENS_DEFAULT = 1024

# USD cost per million tokens (used for cost estimation in usage summary)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "cache_write": 1.00,
        "cache_read": 0.08,
        "output": 4.00,
    },
}

PROJECT_ROOT = Path(__file__).parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# Files that are pure boilerplate (no LLM needed)
BOILERPLATE_FILES = {
    "app.ts",
    "server.ts",
    "package.json",
    "tsconfig.json",
    "prisma.ts",
    ".env.example",
}