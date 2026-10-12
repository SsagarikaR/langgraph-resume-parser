class ScreeningError(Exception):
    """Base class for errors the service raises. The API layer maps each to an HTTP status."""


class ScreeningNotFoundError(ScreeningError):
    pass


class InvalidScreeningInputError(ScreeningError):
    pass


class NotAwaitingReviewError(ScreeningError):
    pass


class NotPausedError(ScreeningError):
    pass


class WorkflowFailedError(ScreeningError):
    pass
