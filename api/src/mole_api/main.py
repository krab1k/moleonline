"""The API: uvicorn mole_api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

import functools
import json
import logging
import sys
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import data, iplog, mole, pdbe, storage, upload
from .config import HttpSettings, Settings
from .csa import CSA, CSAResidue
from .parameters import MoleParameters, PoresParameters, PoresRequest
from .runner import Runner, Step
from .storage import IN_PROGRESS, Job, JobState, Session, SessionState, StoredSession
from .versions import read_mole_version

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


STOPPED_LOAD = "Loading of the structure was stopped."


def find(request: Request, session_id: str) -> StoredSession | JSONResponse:
    """The session, or the response when it does not exist (404) or is deleted (410)"""
    found = storage.load(request.app.state.settings.working_directory, session_id)
    if found is None:
        return problem(404, f"Session [{session_id}] does not exist.")
    if found.deleted:
        return problem(410, "The session has been deleted.")
    return found


STATIC_FILES = Path(__file__).parent / "static"  # served at /: the example inputs (inputs/)
COFACTORS = STATIC_FILES / "inputs" / "cofactors.json"


RETRY_AFTER = 60  # seconds, when the queue is full


def queue_full(request: Request) -> JSONResponse | None:
    """503 when the runs (running and queued) fill the slots and the queue: a new session or job is not accepted"""
    settings: Settings = request.app.state.settings
    if len(request.app.state.runner.runs) < settings.max_concurrent_computations + settings.max_queued_computations:
        return None
    return problem(503, "The server is busy, its queue is full. Please, try again later.", headers={"Retry-After": str(RETRY_AFTER)})


def can_run(s: StoredSession) -> str | None:
    """Why a new job cannot start on the session (409)"""
    if s.state in IN_PROGRESS:
        return "The structure is still being loaded. This job has not been started. Please, try again later."
    if s.state == SessionState.failed:
        return "The structure could not be loaded, no job can run on it."
    if s.last_job is not None and s.last_job.state in IN_PROGRESS:
        return "The previous job has not finished yet. This one has not been started. Please, try again later."
    return None


# the job types as the IP log names them (Mole, Pores), as the former API's endpoints
LOGGED_TYPES = {"tunnels": "Mole", "pores": "Pores"}


class LoggedJob(APIRoute):
    """A new job is logged before its body is validated, so a refused one too; its type from the body (- when the body
    has none)"""

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def logged(request: Request):
            try:
                type = json.loads(await request.body()).get("type")  # the body stays for handle
            except (ValueError, AttributeError):  # not JSON, not an object
                type = None
            kind = LOGGED_TYPES.get(type, "-") if isinstance(type, str) else "-"  # unvalidated: e.g. a list
            iplog.write(request.app.state.settings.working_directory, "Submit", request.client and request.client.host,
                        kind, request.path_params["session_id"])
            return await handle(request)
        return logged


def errors(*codes: int) -> dict:
    """Error responses in the documentation: problem details; 422 for any invalid request"""
    return {code: {"model": Problem, "content": {PROBLEM: {}}} for code in sorted({*codes, 422, 500})}


UPLOAD_FORM = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary",
                            "description": "*.pdb, *.cif, *.pdb.gz or *.cif.gz, at most 30 MB unpacked"}}}}}}}


PORES_PARAMETERS = "pores.json"  # the input of the pores tool
# a process of its own, as MOLE: a stop or the time limit kills it with MEMEMBED; the tests replace it
PORES_TOOL = [sys.executable, "-m", "mole_api.pores"]


def run_mole(settings: Settings, input: Path) -> Step:
    return Step([str(settings.mole), str(input)], errors_on_stderr=True)  # MOLE reports errors on stderr, with exit code 0


def pore_found(directory: Path) -> str | None:
    """Why a pore computation failed although MOLE did not"""
    results = directory / "json" / "data.json"
    if not results.exists():
        return "MOLE did not produce any result."
    if not json.loads(results.read_bytes()).get("Channels", {}).get("Paths"):
        return "No pore was found along the membrane normal."
    return None


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
    assembly: str = pdbe.PREFERRED


def created(request: Request, response: Response, s: StoredSession) -> Session:
    """202 Accepted (the decorator's status) with Location, the session to poll"""
    response.headers["Location"] = request.url_for("get_session", session_id=s.id).path
    return s.public()


class Version(BaseModel):
    api: str  # also of the pores tool, part of the API
    mole: str


async def create_from_upload(request: Request) -> StoredSession | JSONResponse:
    """A session of the uploaded structure, or why it is refused"""
    if int(request.headers.get("content-length") or 0) > upload.MAX_REQUEST_BYTES:  # not read at all
        return problem(413, upload.TOO_LARGE)
    if full := queue_full(request):  # not read either
        return full
    settings: Settings = request.app.state.settings
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
            # checked again: other uploads may have filled the queue while this one was read; from here on nothing
            # awaits until the run is registered, so no other request takes its place
            if full := queue_full(request):
                return full
            s = storage.create(settings.working_directory, request.app.state.mole_version)
            s.structure_file = name
            temp.rename(s.structure_path())
            s.save()
            request.app.state.runner.start(s, None, lambda: [run_mole(settings, mole.load_input(s))])
        finally:
            temp.unlink(missing_ok=True)
    return s


def start_job(request: Request, session_id: str, new: NewTunnelsJob | NewPoresJob,
              write_input: Callable[[StoredSession, Path], list[Step]],
              finish: Callable[[StoredSession, Path], str | None]) -> Job | JSONResponse:
    """A new job of the session, run in the background, or why it is refused. write_input: writes its input into its
    folder, returns its processes; finish: after them, in a thread, returns why the run failed (see Runner)."""
    found = find(request, session_id)
    if isinstance(found, JSONResponse):
        return found
    if problems := new.parameters.problems():
        return problem(422, problems)
    if refused := can_run(found):
        return problem(409, refused)
    if full := queue_full(request):
        return full

    job = found.add_job(new.type, new.parameters.model_dump(), request.app.state.mole_version)
    directory = found.job_directory(job.number)
    try:
        steps = write_input(found, directory)
    except Exception as e:  # noqa: BLE001 (e.g. the structure is missing; the job must not stay queued)
        found.update(job.number, JobState.failed, str(e))
        return problem(500, str(e))
    started = job.model_copy()  # before the run can change it
    request.app.state.runner.start(found, job.number, lambda: steps, finish=lambda: finish(found, directory))
    return started


def create_app(settings: Settings | None = None) -> FastAPI:
    """settings: from the environment by default"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or Settings()  # a wrong setting stops the start
        app.state.mole_version = await read_mole_version(app.state.settings)
        app.state.runner = Runner(app.state.settings)
        app.state.pdbe = pdbe.client()
        app.mount("/", StaticFiles(directory=STATIC_FILES))  # after the endpoints, it would hide them
        yield
        await app.state.runner.stop()
        await app.state.pdbe.aclose()

    # operation ids are the functions' names (get_job), readable in generated clients, not FastAPI's
    # get_job_sessions__session_id__jobs__number__get
    app = FastAPI(title="MOLEonline API", version=version("mole-api"), lifespan=lifespan,
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
    http = settings or HttpSettings()

    @app.middleware("http")
    async def unexpected(request: Request, call_next) -> Response:
        """A middleware, not an exception handler: those of Exception answer outside of all middleware, without CORS"""
        try:
            return await call_next(request)
        except Exception:
            log.exception("Unexpected error of %s %s", request.method, request.url.path)
            return problem(500, "An unexpected error occurred on the server. Please, try again later.")

    app.add_middleware(GZipMiddleware)  # for clients that accept it; not zips, data.json often has megabytes
    # the exposed headers are those the web reads
    app.add_middleware(CORSMiddleware, allow_origins=http.cors_origins, allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Content-Type"], expose_headers=["Location", "Retry-After", "Content-Disposition"])

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
    def get_version(request: Request) -> Version:
        """Versions of the API (with the pores tool) and of MOLE"""
        return Version(api=app.version, mole=request.app.state.mole_version)

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
        settings: Settings = request.app.state.settings
        try:
            if not pdbe.PDB_ID.match(new.pdb_id):
                return problem(422, f"Invalid PDB id [{new.pdb_id}].")
            if not pdbe.ASSEMBLY.match(new.assembly):
                return problem(422, f"Invalid assembly [{new.assembly}]. "
                                    f"Use {pdbe.PREFERRED}, {pdbe.ASYMMETRIC} or an assembly id of the entry.")
            if full := queue_full(request):
                return full
            s = storage.create(settings.working_directory, request.app.state.mole_version, new.pdb_id.lower())
            created_id = s.id
            request.app.state.runner.start(s, None, lambda: [run_mole(settings, mole.load_input(s))],
                                           prepare=lambda: pdbe.download(request.app.state.pdbe, s, new.assembly))
            return created(request, response, s)
        finally:
            iplog.write(settings.working_directory, "Init", request.client and request.client.host, "PDB", created_id)

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
            iplog.write(request.app.state.settings.working_directory, "Init", request.client and request.client.host, "Upload", created_id)

    @sessions.get("/{session_id}", responses=errors(404, 410))
    def get_session(request: Request, session_id: str) -> Session:
        """The session's state: queued, loading, then ready or failed (with the error)"""
        found = find(request, session_id)
        return found if isinstance(found, JSONResponse) else found.public()

    FILE = {200: {"description": "The file, as an attachment"}}

    @sessions.get("/{session_id}/structure", response_class=Response, responses={**errors(404, 410), **FILE})
    def get_structure(request: Request, session_id: str):
        """The session's structure, as loaded (a gzipped upload unpacked)"""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        return data.structure(found) or problem(404, "The structure is not available, it has not been stored.")

    @sessions.get("/{session_id}/data", response_class=Response, responses={**errors(404, 410), **FILE})
    def get_session_data(request: Request, session_id: str):
        """What the load found, MOLE's data.json: the cavities and the origins (tunnel starting points)"""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if found.state != SessionState.ready:
            return problem(404, "The session has no data, its structure has not been loaded.")
        return data.session_data(found) or problem(404, "The data of the session are not available.")

    @functools.cache
    def csa() -> CSA:
        return CSA()  # read on the first request (0.1 s), the tests start faster

    @sessions.get("/{session_id}/active-sites", responses=errors(404, 410))
    def get_active_sites(request: Request, session_id: str) -> list[list[CSAResidue]]:
        """Active sites of the PDB entry from M-CSA (https://www.ebi.ac.uk/thornton-srv/m-csa/, CC BY 4.0), one per
        copy, as tunnel starting points; none for an uploaded structure"""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        return [] if found.pdb_id is None else csa().get(found.pdb_id)

    # cancel and delete stop runs: async, on the event loop as the runs, never in a worker thread (Runner is not
    # thread-safe, and the check, the stop and the state change must not interleave with a run ending)
    @sessions.post("/{session_id}/cancel", responses=errors(404, 409, 410))
    async def cancel_session(request: Request, session_id: str) -> Session:
        """Stops loading the structure (also its download): the session fails. Only a queued or loading session can
        be cancelled; jobs are cancelled on their own."""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if found.state not in IN_PROGRESS or not request.app.state.runner.kill(session_id):
            return problem(409, f"The session is {found.state}, only a queued or loading session can be cancelled.")
        found.update(None, SessionState.failed, STOPPED_LOAD)
        return found.public()

    @sessions.delete("/{session_id}", status_code=204, response_class=Response, responses=errors(404, 410))
    async def delete_session(request: Request, session_id: str):
        """Stops what runs and deletes the session: 410 Gone from then on (its files are kept)"""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        request.app.state.runner.kill(session_id)
        found.delete()
        return Response(status_code=204)

    # creating and cancelling jobs: async, on the event loop as the runs (see cancel_session); creating them is logged
    new_jobs = APIRouter(prefix="/sessions", tags=["jobs"], route_class=LoggedJob)
    job_routes = APIRouter(prefix="/sessions", tags=["jobs"])

    @new_jobs.post("/{session_id}/jobs", status_code=202, responses=errors(404, 409, 410, 503))
    async def create_job(request: Request, response: Response, session_id: str, new: NewJob) -> Job:
        """A job on the session's structure, run in the background: queued, running, then finished, failed or
        cancelled. Poll the job (Location). One job runs on a session at a time (409 while another one is queued or
        running); it waits for a slot, queued, and 503 (Retry-After) when the queue is full."""
        settings: Settings = request.app.state.settings

        if isinstance(new, NewTunnelsJob):
            parameters = new.parameters

            def write_input(s: StoredSession, directory: Path) -> list[Step]:
                return [run_mole(settings, mole.computation_input(s, parameters, directory))]

            def finish(s: StoredSession, directory: Path) -> str | None:
                mole.zip_report(directory, s.structure_path())
                return None
        else:
            pores = new.parameters

            def write_input(s: StoredSession, directory: Path) -> list[Step]:
                tool_input = directory / PORES_PARAMETERS
                tool_input.write_text(PoresParameters(
                    Structure=str(s.structure_path()), PdbId=s.pdb_id if s.opm else None, WorkingDirectory=str(directory),
                    Membrane=str(directory / "membrane.json"), Memembed=str(settings.memembed), OpmCache=str(settings.opm_cache),
                    **pores.model_dump(),
                ).model_dump_json(indent=2))
                return [Step([*PORES_TOOL, str(tool_input)]), run_mole(settings, directory / mole.INPUT)]

            def finish(s: StoredSession, directory: Path) -> str | None:
                if problem := pore_found(directory):
                    return problem
                mole.zip_report(directory, s.structure_path())
                return None

        started = start_job(request, session_id, new, write_input, finish)
        if isinstance(started, JSONResponse):
            return started
        response.headers["Location"] = request.url_for("get_job", session_id=session_id, number=started.number).path
        return started

    @job_routes.get("/{session_id}/jobs/{number}", responses=errors(404, 410))
    def get_job(request: Request, session_id: str, number: int) -> Job:
        """The job's state: queued, running, then finished, failed (with the error) or cancelled"""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        return wanted

    @job_routes.get("/{session_id}/jobs/{number}/results/{format}", response_class=Response, responses={**errors(404, 410), **FILE})
    def get_job_result(request: Request, session_id: str, number: int, format: data.ResultFormat):
        """A result of the job: data (MOLE's data.json, what the web shows), report (a zip of all), pymol, chimera, vmd
        (scripts of the viewers), pdb (a zip of the tunnels' profiles), membrane (of a pore job, also when MOLE then
        failed). Only a finished job has results."""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        # MOLE's files appear while it runs, a failed run leaves some; the membrane is complete once written
        if format != data.ResultFormat.membrane and wanted.state != JobState.finished:
            return problem(404, f"Job {number} has no results, it is {wanted.state}.")
        return data.job_result(found, number, format) or problem(404, f"The job has no {format} results.")

    @job_routes.post("/{session_id}/jobs/{number}/cancel", responses=errors(404, 409, 410))
    async def cancel_job(request: Request, session_id: str, number: int) -> Job:
        """Stops the job: cancelled. Only a queued or running job can be cancelled."""
        found = find(request, session_id)
        if isinstance(found, JSONResponse):
            return found
        if (wanted := found.job(number)) is None:
            return problem(404, f"Job {number} does not exist.")
        # one job runs on a session at a time, the last one; the session's run is its
        if wanted.state not in IN_PROGRESS or not request.app.state.runner.kill(session_id):
            return problem(409, f"The job is {wanted.state}, only a queued or running job can be cancelled.")
        found.update(number, JobState.cancelled)
        return found.job(number)

    app.include_router(sessions)
    app.include_router(new_jobs)
    app.include_router(job_routes)
    return app


app = create_app()
