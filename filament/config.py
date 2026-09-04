"""Environment-based settings.

Every setting has a default here and is overridable via an environment
variable. The CLI may override `backend` via a flag; nothing else is
configurable on the command line in v1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BACKEND = "rosie"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_ROSIE_MODEL = "Qwen3-Coder-30B-A3B-Instruct"


@dataclass
class Config:
    """Runtime settings, one per environment variable (see `load_config`).

    Attributes:
        backend: Which model client to build: `rosie` or `anthropic`.
        rosie_endpoint: Base URL of Rosie's OpenAI-compatible API.
        rosie_model: Model name to request from Rosie.
        anthropic_api_key: Anthropic API key; required for that backend.
        anthropic_model: Anthropic model name.
        workdir: Directory the file and shell tools are confined to.
    """

    backend: str
    rosie_endpoint: str
    rosie_model: str
    anthropic_api_key: str
    anthropic_model: str
    workdir: str


def load_config() -> Config:
    """Read settings from the environment, applying defaults."""
    return Config(
        backend=os.environ.get("FILAMENT_BACKEND", DEFAULT_BACKEND),
        rosie_endpoint=os.environ.get("FILAMENT_ROSIE_ENDPOINT", ""),
        rosie_model=os.environ.get("FILAMENT_ROSIE_MODEL", DEFAULT_ROSIE_MODEL),
        anthropic_api_key=os.environ.get("FILAMENT_ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get(
            "FILAMENT_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL
        ),
        workdir=os.environ.get("FILAMENT_WORKDIR") or os.getcwd(),
    )
