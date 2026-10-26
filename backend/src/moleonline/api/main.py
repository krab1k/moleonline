"""The API: uvicorn moleonline.api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

import asyncio
import functools
import json
import logging
import uuid
from contextlib import asynccontextmanager
from http import HTTPStatus
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core import entries, storage
from ..core.config import ApiSettings
from ..core.parameters import MoleParameters, PoresRequest
from ..core.storage import Job, JobState, Refused, Session, SessionState, StoredSession
from . import data, upload
from .csa import CSA, CSAResidue

log = logging.getLogger("uvicorn.error")  # the API's log, in uvicorn's format, one line per event


class FieldError(BaseModel):
    field: str  # where in the request, e.g. parameters.Cavity.ProbeRadius, path.format
    message: str


class Problem(BaseModel):
    """Why a request failed: problem details (RFC 9457, application/problem+json). type is about:blank, so title is the
    HTTP status' phrase; detail says what was wrong."""
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    errors: list[FieldError] | None = None  # the invalid fields of a request that failed validation (422)


PROBLEM = "application/problem+json"


def problem(status: int, detail: str, errors: list[FieldError] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body = Problem(title=HTTPStatus(status).phrase, status=status, detail=detail, errors=errors)
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status, headers=headers, media_type=PROBLEM)


async def find(request: Request, session_id: str) -> StoredSession | JSONResponse:
    """The session, or the response when it does not exist (404) or is deleted (410)"""
    found = await storage.get(request.app.state.db, request.app.state.settings.working_directory, session_id)
    if found is None:
        return problem(404, f"Session [{session_id}] does not exist.")
    if found.deleted:
        return problem(410, "The session has been deleted.")
    return found


STATIC_FILES = Path(__file__).parent / "static"  # served at /: the example inputs (inputs/)
COFACTORS = STATIC_FILES / "inputs" / "cofactors.json"


CLEANUP_SECONDS = 10.0  # at most, how often runs whose lease expired are failed as interrupted


async def clean_up(db: storage.Database, timeout: float) -> None:
    """Fails the runs whose lease expired (their worker crashed, was killed, lost its node), until cancelled"""
    while True:
        await asyncio.sleep(min(CLEANUP_SECONDS, timeout / 3))
        try:
            if expired := await storage.expire(db, timeout):
                log.warning("%d runs were interrupted, their workers stopped renewing their leases", expired)
        except Exception as e:  # noqa: BLE001 (e.g. the database is not available for a moment; tried again)
            log.warning("Runs whose lease expired could not be looked for: %s", e)


# the job types as the request log names them (Mole, Pores), as the former API's endpoints
LOGGED_TYPES = {"tunnels": "Mole", "pores": "Pores"}


def errors(*codes: int) -> dict:
    """Error responses in the documentation: problem details; 422 for any invalid request"""
    return {code: {"model": Problem, "content": {PROBLEM: {}}} for code in sorted({*codes, 422, 500})}


UPLOAD_FORM = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary",
                            "description": "*.pdb, *.cif, *.pdb.gz or *.cif.gz, at most 30 MB unpacked"}}}}}}}


class NewTunnelsJob(BaseModel):
    """Tunnels (pores with PoresAuto, PoresMerged or exits without an origin): MOLE's parameters"""
    model_config = ConfigDict(extra="forbid")
    type: Literal["tunnels"]
    parameters: MoleParameters


class NewPoresJob(BaseModel):
    """A pore across the membrane: the pores tool places the membrane (from OPM for the preferred assembly of a PDB
    entry, otherwise predicted by MEMEMBED) and MOLE computes the pore along its normal"""
    model_config = ConfigDict(extra="forbid")
    type: Literal["pores"]
    parameters: PoresRequest


NewJob = Annotated[NewTunnelsJob | NewPoresJob, Body(discriminator="type")]


class NewSession(BaseModel):
    """A session of a PDB entry"""
    model_config = ConfigDict(extra="forbid")
    pdb_id: str
    # preferred: PDBe's preferred assembly, the one OPM has; asymmetric: the asymmetric unit; or an assembly id
    assembly: str = entries.PREFERRED


def created(request: Request, response: Response, s: StoredSession) -> Session:
    """202 Accepted (the decorator's status) with Location, the session to poll"""
    response.headers["Location"] = request.url_for("get_session", session_id=s.id).path
    return s.public()


class Version(BaseModel):
    api: str  # of the backend: the API and the workers, with the pores tool
    mole: str | None  # of the MOLE that took the latest run; None before the first one


async def create_from_upload(request: Request) -> StoredSession | JSONResponse:
    """A session of the uploaded structure, or why it is refused"""
    if int(request.headers.get("content-length") or 0) > upload.MAX_REQUEST_BYTES:  # not read at all
        return problem(413, upload.TOO_LARGE)
    settings: ApiSettings = request.app.state.settings
    if await storage.queue_is_full(request.app.state.db, settings.max_outstanding_computations):  # not read either
        raise storage.QUEUE_FULL
    async with request.form() as form:
        file = form.get("file")
        if not isinstance(file, UploadFile) or not file.size:
            return problem(422, upload.NO_FILE)
        if (name := upload.stored_name(file.filename or "")) is None:
            return problem(422, upload.UNSUPPORTED)
        gzipped = (file.filename or "").lower().endswith(".gz")
        temp = settings.working_directory / f".upload-{uuid.uuid4().hex}"  # a session only for a structure that fits
        try:
            if refused := await run_in_threadpool(upload.store, file.file, gzipped, temp):
                return problem(*refused)
            # the queue is checked again, with the session added in one step: other uploads may have filled it
            s = await storage.create(request.app.state.db, settings.working_directory, settings.max_outstanding_computations,
                                     structure=temp, structure_file=name)
        finally:
            temp.unlink(missing_ok=True)
    return s


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """settings: from the environment by default"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or ApiSettings()  # a wrong setting stops the start
        app.state.db = storage.Database(app.state.settings.database_url)
        await app.state.db.migrate()
        # the runs are run by the workers (python -m moleonline.worker); the API fails those whose lease expired
        cleanup = asyncio.create_task(clean_up(app.state.db, app.state.settings.worker_timeout_seconds))
        app.mount("/", StaticFiles(directory=STATIC_FILES))  # after the endpoints, it would hide them
        yield
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        await app.state.db.close()

    # operation ids are the functions' names (get_job), readable in generated clients, not FastAPI's
    # get_job_sessions__session_id__jobs__number__get
    app = FastAPI(title="MOLEonline API", version=version("moleonline-backend"), lifespan=lifespan,
                  generate_unique_id_function=lambda route: route.name)

    def openapi() -> dict:
        """FastAPI's description, the errors' schema under their media type (it puts a response's model under
        application/json, the problem's media type stays without a schema)"""
        if app.openapi_schema is None:
            description = FastAPI.openapi(app)
            for operation in (op for path in description["paths"].values() for op in path.values()):
                for status, response in operation.get("responses", {}).items():
                    content = response.get("content", {})
                    if int(status) >= 400 and PROBLEM in content:
                        content[PROBLEM] = content.pop("application/json")
        return app.openapi_schema
    app.openapi = openapi
    app.add_middleware(GZipMiddleware)  # for clients that accept it; not zips, data.json often has megabytes

    @app.exception_handler(Exception)
    async def unexpected(request: Request, e: Exception) -> JSONResponse:  # logged by uvicorn
        return problem(500, "An unexpected error occurred on the server. Please, try again later.")

    @app.exception_handler(Refused)
    async def refused(request: Request, e: Refused) -> JSONResponse:
        """A new session or job not accepted: 409, 410, 503 (with Retry-After)"""
        return problem(e.status, e.detail, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        """422 with the invalid fields"""
        def where(loc: tuple) -> str:  # the field in the body; without the job type, the tag of NewJob's union
            parts = [str(part) for part in loc if part != "body"]
            return ".".join(parts[1:] if parts and parts[0] in LOGGED_TYPES else parts)
        fields = [FieldError(field=where(error["loc"]), message=error["msg"]) for error in e.errors()]
        return problem(422, "Invalid request. " + "; ".join(f"{f.field}: {f.message}" for f in fields), fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
        """The framework's errors (no such path, a wrong method) as problems too, with their headers (Allow of a 405)"""
        if e.status_code < 400:
            return await http_exception_handler(request, e)
        return problem(e.status_code, str(e.detail), headers=e.headers)

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("docs")  # relative: also behind a proxy's prefix (/api/)

    @app.get("/version")
    async def get_version(request: Request) -> Version:
        """Versions of the backend (the API, the workers with the pores tool) and of the MOLE that took the latest run"""
        return Version(api=app.version, mole=await storage.mole_version(request.app.state.db))

    @app.get("/cofactors")
    def get_cofactors() -> dict[str, str]:
        """Cofactors by residue name, with the PatternQuery expression of their atoms, as tunnel starting points"""
        return json.loads(COFACTORS.read_text())

    sessions = APIRouter(prefix="/sessions", tags=["sessions"])

    @sessions.post("", status_code=202, responses=errors(503))
    async def create_session(request: Request, response: Response, new: NewSession) -> Session:
        """A session of a PDB entry: downloaded from PDBe and loaded by MOLE in the background. Poll the session
        (Location) until it is ready or failed."""
        created_id = None
        settings: ApiSettings = request.app.state.settings
        try:
            if not entries.PDB_ID.match(new.pdb_id):
                return problem(422, f"Invalid PDB id [{new.pdb_id}].")
            if not entries.ASSEMBLY.match(new.assembly):
                return problem(422, f"Invalid assembly [{new.assembly}]. "
                                    f"Use {entries.PREFERRED}, {entries.ASYMMETRIC} or an assembly id of the entry.")
            s = await storage.create(request.app.state.db, settings.working_directory, settings.max_outstanding_computations,
                                     pdb_id=new.pdb_id.lower(), assembly=new.assembly)
            created_id = s.id
            return created(request, response, s)
        finally:
            await storage.log_request(request.app.state.db, "Init", request.client and request.client.host, "PDB", created_id)

    @sessions.post("/upload", status_code=202, responses=errors(413, 503), openapi_extra=UPLOAD_FORM)
    async def upload_session(request: Request, response: Response) -> Session:
        """A session of an uploaded structure, loaded by MOLE in the background; a gzipped file is stored unpacked.
        Poll the session (Location) until it is ready or failed."""
        created_id = None
        try:
            found = await create_from_upload(request)
            if isinstance(found, JSONResponse):
                return found
            created_id = found.id
            return created(request, response, found)
        finally:
            await storage.log_request(request.app.state.db, "Init", request.client and request.client.host, "Upload", created_id)

    @sessions.get("/{session_id}", responses=errors(404, 410))
    async def get_session(request: Request, session_id: str) -> Session:
        """The session's state: queued, loading, then ready or failed (with the error)"""
        found = await find(request, session_id)
        return found if isinstance(found, JSONResponse) else found.public()

    FILE = {200: {"description": "The file, as an attachment"}}

    @sessions.get("/{session_id}/structure", response_class=Response, responses={**errors(404, 410), **FILE})
    async def get_structure(request: Request, session_id: str):
        """The session's structure, as loaded (a gzipped upload unpacked)"""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        return data.structure(found) or problem(404, "The structure is not available, it has not been stored.")

    @sessions.get("/{session_id}/data", response_class=Response, responses={**errors(404, 410), **FILE})
    async def get_session_data(request: Request, session_id: str):
        """What the load found, MOLE's data.json: the cavities and the origins (tunnel starting points)"""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if found.state != SessionState.ready:
            return problem(404, "The session has no data, its structure has not been loaded.")
        return data.session_data(found) or problem(404, "The data of the session are not available.")

    @functools.cache
    def csa() -> CSA:
        return CSA()  # read on the first request (0.1 s), the tests start faster

    @sessions.get("/{session_id}/active-sites", responses=errors(404, 410))
    async def get_active_sites(request: Request, session_id: str) -> list[list[CSAResidue]]:
        """Active sites of the PDB entry from M-CSA (https://www.ebi.ac.uk/thornton-srv/m-csa/, CC BY 4.0), one per
        copy, as tunnel starting points; none for an uploaded structure"""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        return [] if found.pdb_id is None else csa().get(found.pdb_id)

    @sessions.post("/{session_id}/cancel", responses=errors(404, 409, 410))
    async def cancel_session(request: Request, session_id: str) -> Session:
        """Stops loading the structure (also its download): the session fails. Only a queued or loading session can
        be cancelled; jobs are cancelled on their own."""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if not await storage.cancel_load(request.app.state.db, session_id):
            return problem(409, f"The session is {found.state}, only a queued or loading session can be cancelled.")
        cancelled = await find(request, session_id)  # deleted meanwhile: 410
        return cancelled if isinstance(cancelled, JSONResponse) else cancelled.public()

    @sessions.delete("/{session_id}", status_code=204, response_class=Response, responses=errors(404, 410))
    async def delete_session(request: Request, session_id: str):
        """Stops what runs and deletes the session: 410 Gone from then on (its files are kept)"""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        await storage.delete(request.app.state.db, session_id)
        return Response(status_code=204)

    job_routes = APIRouter(prefix="/sessions", tags=["jobs"])

    @job_routes.post("/{session_id}/jobs", status_code=202, responses=errors(404, 409, 410, 503))
    async def create_job(request: Request, response: Response, session_id: str, new: NewJob) -> Job:
        """A job on the session's structure, run in the background: queued, running, then finished, failed or
        cancelled. Poll the job (Location). One job runs on a session at a time (409 while another one is queued or
        running); it waits for a worker, queued, and 503 (Retry-After) when the queue is full."""
        settings: ApiSettings = request.app.state.settings
        await storage.log_request(request.app.state.db, "Submit", request.client and request.client.host, LOGGED_TYPES[new.type],
                                  session_id)
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if problems := new.parameters.problems():
            return problem(422, problems)
        job = await storage.add_job(request.app.state.db, found, new.type, new.parameters.model_dump(),
                                    settings.max_outstanding_computations)
        response.headers["Location"] = request.url_for("get_job", session_id=session_id, number=job.number).path
        return job

    @job_routes.get("/{session_id}/jobs/{number}", responses=errors(404, 410))
    async def get_job(request: Request, session_id: str, number: int) -> Job:
        """The job's state: queued, running, then finished, failed (with the error) or cancelled"""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        return wanted

    @job_routes.get("/{session_id}/jobs/{number}/results/{format}", response_class=Response, responses={**errors(404, 410), **FILE})
    async def get_job_result(request: Request, session_id: str, number: int, format: data.ResultFormat):
        """A result of the job: data (MOLE's data.json, what the web shows), report (a zip of all), pymol, chimera, vmd
        (scripts of the viewers), pdb (a zip of the tunnels' profiles), membrane (of a pore job, also when MOLE then
        failed). Only a finished job has results."""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        # MOLE's files appear while it runs, a failed run leaves some; the membrane is complete once written
        if format != data.ResultFormat.membrane and wanted.state != JobState.finished:
            return problem(404, f"Job {number} has no results, it is {wanted.state}.")
        # in a thread: the tunnels' profiles (pdb) are zipped here
        return await run_in_threadpool(data.job_result, found, number, format) or problem(404, f"The job has no {format} results.")

    @job_routes.post("/{session_id}/jobs/{number}/cancel", responses=errors(404, 409, 410))
    async def cancel_job(request: Request, session_id: str, number: int) -> Job:
        """Stops the job: cancelled. Only a queued or running job can be cancelled."""
        found = await find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        if not await storage.cancel_job(request.app.state.db, session_id, number):
            return problem(409, f"The job is {wanted.state}, only a queued or running job can be cancelled.")
        cancelled = await find(request, session_id)  # deleted meanwhile: 410
        return cancelled if isinstance(cancelled, JSONResponse) else cancelled.job(number)

    app.include_router(sessions)
    app.include_router(job_routes)
    return app


app = create_app()
