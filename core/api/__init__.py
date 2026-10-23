"""Versioned JSON API for the Eden school system.

Everything under ``core.api`` is deliberately dependency-free: the package
reuses Django forms, services, and permission helpers rather than introducing
a second serialization/authorization stack. See ``docs/API.md`` for the
contract.
"""
