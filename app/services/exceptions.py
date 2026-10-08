class ScreeningError(Exception):
    """Base class for errors the service raises. The API layer maps each to an HTTP status."""


class ScreeningNotFound(ScreeningError):
    pass


class InvalidScreeningInput(ScreeningError):
    pass


class NotAwaitingReview(ScreeningError):
    pass


class NotPaused(ScreeningError):
    pass


class WorkflowFailed(ScreeningError):
    pass
