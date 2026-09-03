"""Per-request context (the signed-in user's email) for templates to read
without threading it through every page function."""
from contextvars import ContextVar

current_email: ContextVar[str | None] = ContextVar("current_email", default=None)
