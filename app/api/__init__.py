def __getattr__(name: str):
    # Lets `uvicorn app.api:app` keep working without building the app (and reading settings)
    # whenever anything imports this package
    if name == "app":
        from app.main import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app"]
