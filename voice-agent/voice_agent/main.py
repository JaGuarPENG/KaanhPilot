"""Stable HTTP entry point; composition lives in bootstrap and api."""
from .api.app import create_app

__all__ = ["create_app"]
