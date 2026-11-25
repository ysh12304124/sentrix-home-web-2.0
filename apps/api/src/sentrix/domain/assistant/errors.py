"""Conversation lifecycle errors shared by persistence and application layers."""

from sentrix.domain.errors import DomainError


class ConversationBusyError(DomainError):
    code = "conversation_busy"
    status_code = 409


class ConversationNotFoundError(DomainError):
    code = "conversation_not_found"
    status_code = 404
