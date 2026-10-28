class InvalidInputError(Exception):
    """Raised by graph nodes when the user's input (resume / JD) is unusable.

    The service maps this to a 400. Any other exception is a workflow failure (500);
    catching ValueError instead would also swallow pydantic/LLM parsing errors.
    """


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


class ScreeningBusyError(ScreeningError):
    """Another request is running or deleting this screening right now."""


class WorkflowFailedError(ScreeningError):
    pass


class ServerBusyError(ScreeningError):
    """The server is already running as many workflows as it allows; retry shortly."""


class BudgetExceededError(ServerBusyError):
    """The server has used its daily LLM token budget; new runs wait until it frees up."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class LlmUnavailableError(ServerBusyError):
    """The LLM circuit breaker is open: too many consecutive failures, so this call was skipped
    instead of being sent (and left to time out) while the provider is down."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after
