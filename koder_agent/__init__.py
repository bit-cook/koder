"""Koder Agent - An advanced AI coding assistant and interactive CLI tool."""

import importlib

from .litellm_cost_map import (
    configure_litellm_local_model_cost_map,
    install_vendored_litellm_model_cost_map,
)

configure_litellm_local_model_cost_map()

_litellm = importlib.import_module("litellm")
install_vendored_litellm_model_cost_map(_litellm)

__version__ = "0.5.2"


def main(*args, **kwargs):
    from .cli import main as _main

    return _main(*args, **kwargs)


def run(*args, **kwargs):
    from .cli import run as _run

    return _run(*args, **kwargs)


__all__ = ["main", "run", "__version__"]
