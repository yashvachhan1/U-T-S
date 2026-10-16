"""Persistence layer.

Everything above this package asks `store` for data and never touches SQLite,
Parquet or Postgres directly. That is what makes the planned move to
PostgreSQL (and possibly TimescaleDB on the VPS) a change in one place.
"""
from . import ticks  # noqa: F401
