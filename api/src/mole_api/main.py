"""The API: uvicorn mole_api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request
from pydantic import BaseModel

from .config import Settings
from .versions import read_versions


class Version(BaseModel):
    APIVersion: str
    MoleVersion: str
    PoresVersion: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """settings: from the environment by default"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or Settings()  # a wrong setting stops the start
        app.state.versions = await read_versions(app.state.settings)
        yield

    app = FastAPI(title="MOLEonline API", version=version("mole-api"), lifespan=lifespan)

    @app.get("/Version")
    def get_version(request: Request) -> Version:
        """Versions of the API and of the MOLE and pores tool that compute"""
        tools = request.app.state.versions
        return Version(APIVersion=app.version, MoleVersion=tools.mole, PoresVersion=tools.pores)

    return app


app = create_app()
