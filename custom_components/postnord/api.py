"""Compatibility imports for the tracking-code source.

New code imports from :mod:`postnord.tracking.api`; this module preserves the
pre-account public import path for custom automations and older tests.
"""
from .tracking.api import PostNordApiClient, PostNordApiError

__all__ = ["PostNordApiClient", "PostNordApiError"]
