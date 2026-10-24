class Invalid(ValueError):
    """User-facing validation error (becomes HTTP 400 / a tool error)."""


class NotFound(LookupError):
    """Becomes HTTP 404 / a tool error."""
