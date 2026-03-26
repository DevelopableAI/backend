"""
Provider registry for the Deployment Agent.

Import get_provider() to resolve a provider name to a configured instance.
"""

from pathlib import Path
from typing import Any

from .base import BaseProvider


PROVIDER_MAP: dict[str, str] = {
    "aws": "AWS ECS Fargate",
    "heroku": "Heroku",
    "gcp": "GCP Cloud Run",
}


def get_provider(name: str, out_dir: Path, **kwargs: Any) -> BaseProvider:
    """
    Resolve a provider name to a configured BaseProvider instance.

    Args:
        name:    Provider slug — one of "aws", "heroku", "gcp".
        out_dir: Generated project directory (used for Dockerfile path etc.).
        **kwargs: Provider-specific keyword arguments forwarded to the constructor
                  (e.g. ``region`` for AWS, ``app_name`` for Heroku).

    Returns:
        An uninitialised BaseProvider subclass instance.
        Call detect_credentials() / collect_credentials() then configure()
        before calling deploy().

    Raises:
        ValueError: If ``name`` is not a recognised provider slug.
    """
    name = name.lower().strip()

    if name == "aws":
        from .aws import AWSProvider
        return AWSProvider(out_dir=out_dir, **kwargs)

    if name == "heroku":
        from .heroku import HerokuProvider
        return HerokuProvider(out_dir=out_dir, **kwargs)

    if name == "gcp":
        from .gcp import GCPProvider
        return GCPProvider(out_dir=out_dir, **kwargs)

    known = ", ".join(PROVIDER_MAP.keys())
    raise ValueError(f"Unknown provider '{name}'. Choose from: {known}")


__all__ = ["get_provider", "PROVIDER_MAP", "BaseProvider"]
