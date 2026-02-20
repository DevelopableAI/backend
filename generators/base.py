from abc import ABC, abstractmethod
from typing import Any


class BaseGenerator(ABC):

    @abstractmethod
    def render(self, **kwargs) -> str:
        pass

    def _cleanup_markdown(self, text: str) -> str:
        """Strip markdown code fences if an LLM returns them."""
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
