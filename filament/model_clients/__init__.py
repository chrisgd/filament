"""Model client package: the factory that picks a client by backend.

To add a backend: write a client module conforming to the `ModelClient`
Protocol, then add a branch to `build_client` below keyed on a new
`FILAMENT_BACKEND` value. Nothing in the agent loop changes.
"""

from __future__ import annotations

from ..config import Config
from .anthropic import AnthropicClient
from .base import ModelClient, ModelResponseError
from .rosie import RosieClient

__all__ = ["ModelClient", "ModelResponseError", "build_client"]


def build_client(config: Config) -> ModelClient:
    """Construct the model client selected by `config.backend`."""
    if config.backend == "rosie":
        return RosieClient(config.rosie_endpoint, config.rosie_model)
    if config.backend == "anthropic":
        return AnthropicClient(config.anthropic_api_key, config.anthropic_model)
    raise ValueError(f"unknown backend: {config.backend}")
