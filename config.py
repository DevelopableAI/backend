import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USER = os.getenv("GITHUB_USER", "")
MODEL = "claude-sonnet-4-6"
TEMPERATURE_LLM = 0.2

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