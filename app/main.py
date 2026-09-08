"""Minimal ASGI entry point for the foundation image."""

from fastapi import FastAPI

from app import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="OpsPilot", version=__version__)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "api", "version": __version__}

    return app


app = create_app()
