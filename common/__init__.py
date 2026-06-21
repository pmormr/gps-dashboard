"""Shared core helpers used across the dashboard's subsystems.

A small, dependency-light library factored out of the per-subsystem duplication
(API request validation, gpsd socket access, subprocess/systemctl wrappers, CLI
plumbing). Imported by ``api``, ``tools``, ``processor``, and future systems.
"""
