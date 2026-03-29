"""Compatibility wrapper for the documented db.schema import path."""

from db_schema import (
    DB_PATH,
    DEFAULT_SETTINGS,
    get_db,
    get_settings,
    init_db,
    save_settings,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_SETTINGS",
    "get_db",
    "get_settings",
    "init_db",
    "save_settings",
]
