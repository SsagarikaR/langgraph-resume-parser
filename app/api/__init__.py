def __getattr__(name: str):
    # Lets `uvicorn app.api:app` keep working without building the app (and reading settings)
    # whenever anything imports a module of this package, such as app.api.auth
    if name == "app":
        from app.api.main import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app"]
