"""The API: uvicorn mole_api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import computation
from .computation import Report, Status
from .config import Settings
from .versions import read_versions


def respond(report: Report, status_code: int = 200) -> JSONResponse:
    return JSONResponse(report.model_dump(mode="json"), status_code)


def error(computation_id: str | None, status_code: int, message: str, submit_id: int = 0) -> JSONResponse:
    return respond(Report(ComputationId=computation_id, SubmitId=submit_id, Status=Status.Error, ErrorMsg=message), status_code)


ERRORS = {code: {"model": Report} for code in (404, 410, 500)}  # in the documentation


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

    @app.exception_handler(Exception)
    async def unexpected(request: Request, e: Exception) -> JSONResponse:  # logged by uvicorn
        return error(None, 500, "An unexpected error occurred on the server. Please, try again later.")

    @app.get("/Version")
    def get_version(request: Request) -> Version:
        """Versions of the API and of the MOLE and pores tool that compute"""
        tools = request.app.state.versions
        return Version(APIVersion=app.version, MoleVersion=tools.mole, PoresVersion=tools.pores)

    @app.get("/Status/{computation_id}", responses=ERRORS)
    def get_status(request: Request, computation_id: str, submitId: int = 0) -> Report:
        """State of a submission: -1 the last one, 0 (default) the first one, the load"""
        found = computation.load(request.app.state.settings.working_directory, computation_id)
        if found is None:
            return respond(computation.not_exists(computation_id), 404)
        if found.is_deleted():
            return error(computation_id, 410, "The computation has been deleted.")
        report = found.report(submitId)
        if not any(u.SubmitId == report.SubmitId for u in found.ComputationUnits):
            return respond(report, 404)  # "SubmitId N not found."
        return report

    return app


app = create_app()
