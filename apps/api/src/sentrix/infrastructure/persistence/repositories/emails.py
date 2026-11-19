"""Compatibility import for pre-unification clients; no email-specific repository."""
from sentrix.infrastructure.persistence.repositories.text_memories import SqliteTextMemoryRepository

SqliteEmailRepository = SqliteTextMemoryRepository
