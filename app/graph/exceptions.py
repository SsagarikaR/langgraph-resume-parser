class InvalidInputError(Exception):
    """Raised by graph nodes when the user's input (resume / JD) is unusable.

    The service maps this to a 400. Any other exception is a workflow failure (500);
    catching ValueError instead would also swallow pydantic/LLM parsing errors.
    """
