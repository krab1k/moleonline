"""The API: uvicorn mole_api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

import uuid
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from pydantic import BaseModel

from . import computation, mole, upload
from .computation import Report, Status
from .config import Settings
from .runner import Runner
from .versions import read_versions


def respond(report: Report, status_code: int = 200) -> JSONResponse:
    return JSONResponse(report.model_dump(mode="json"), status_code)


def error(computation_id: str | None, status_code: int, message: str, submit_id: int = 0) -> JSONResponse:
    return respond(Report(ComputationId=computation_id, SubmitId=submit_id, Status=Status.Error, ErrorMsg=message), status_code)


def errors(*codes: int) -> dict:
    """Error responses in the documentation"""
    return {code: {"model": Report} for code in (*codes, 500)}


UPLOAD_FORM = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary", "description": "*.pdb, *.cif, *.pdb.gz or *.cif.gz, at most 30 MB unpacked"}}}}}}}


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
        app.state.runner = Runner(app.state.settings)
        yield
        await app.state.runner.stop()

    app = FastAPI(title="MOLEonline API", version=version("mole-api"), lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, e: Exception) -> JSONResponse:  # logged by uvicorn
        return error(None, 500, "An unexpected error occurred on the server. Please, try again later.")

    @app.get("/Version")
    def get_version(request: Request) -> Version:
        """Versions of the API and of the MOLE and pores tool that compute"""
        tools = request.app.state.versions
        return Version(APIVersion=app.version, MoleVersion=tools.mole, PoresVersion=tools.pores)

    @app.get("/Status/{computation_id}", responses=errors(404, 410))
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

    @app.post("/Init", responses=errors(400, 413), openapi_extra=UPLOAD_FORM)
    async def init_upload(request: Request) -> Report:
        """A computation of an uploaded structure, loaded by MOLE in the background (Initializing, then Initialized
        or FailedInitialization, see Status). A gzipped file is stored unpacked."""
        if int(request.headers.get("content-length") or 0) > upload.MAX_REQUEST_BYTES:  # not read at all
            return error(None, 413, upload.TOO_LARGE)
        settings: Settings = request.app.state.settings
        async with request.form() as form:
            file = form.get("file")
            if not isinstance(file, UploadFile) or not file.size:
                return error(None, 400, upload.NO_FILE)
            name = Path(file.filename or "").name  # from the client, never let it leave the folder
            if not upload.STRUCTURE_NAME.search(name.lower()):
                return error(None, 400, upload.UNSUPPORTED)
            gzipped = name.lower().endswith(".gz")
            temp = settings.working_directory / f".upload-{uuid.uuid4().hex}"  # a computation only for a structure that fits
            try:
                if refused := await run_in_threadpool(upload.store, file.file, gzipped, temp):
                    return error(None, *refused)
                c = computation.create(settings.working_directory, request.app.state.versions.mole)
                c.StructureFile = name[:-3] if gzipped else name
                temp.rename(c.structure_path())
                c.save()
            finally:
                temp.unlink(missing_ok=True)
        try:
            request.app.state.runner.start(c, [[str(settings.mole), str(mole.load_input(c))]], init=True)
        except OSError as e:  # the input cannot be written
            c.change_status(Status.FailedInitialization, str(e))
        return c.report()

    return app


app = create_app()
