"""Media isolation is independent of processing status and irreversible in phase 2."""

from sentrix.domain.errors import DomainError


class MediaIsolated(DomainError):
    code = "media_isolated"
    status_code = 409


class DeletionNotFound(DomainError):
    code = "not_found"
    status_code = 404


class InvalidDeletion(DomainError):
    code = "invalid_deletion"
    status_code = 409
