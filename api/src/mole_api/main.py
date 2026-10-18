"""The API: uvicorn mole_api.main:app. Documentation at /docs, the OpenAPI description at /openapi.json."""

import functools
import json
import sys
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import computation, data, iplog, mole, pdbe, upload
from .csa import CSA, CSAResidue
from .paths import AspNetPaths
from .computation import Computation, Report, Status
from .config import Settings
from .parameters import (DEFAULT_PORES_INTERIOR_THRESHOLD, DEFAULT_PORES_PROBE_RADIUS, MoleParameters, PoresParameters,
                         PoresRequest)
from .runner import Runner, Step
from .versions import read_mole_version


def respond(report: Report, status_code: int = 200) -> JSONResponse:
    return JSONResponse(report.model_dump(mode="json"), status_code)


def error(computation_id: str | None, status_code: int, message: str, submit_id: int = 0) -> JSONResponse:
    return respond(Report(ComputationId=computation_id, SubmitId=submit_id, Status=Status.Error, ErrorMsg=message), status_code)


STOPPED_LOAD = "Loading of the structure was stopped."


def find(request: Request, computation_id: str) -> Computation | JSONResponse:
    """The computation, or the response when it does not exist (404) or is deleted (410)"""
    found = computation.load(request.app.state.settings.working_directory, computation_id)
    if found is None:
        return respond(computation.not_exists(computation_id), 404)
    if found.is_deleted():
        return error(computation_id, 410, "The computation has been deleted.")
    return found


STATIC_FILES = Path(__file__).parent / "static"  # served at /: the example inputs, inputs/cofactors.json for the web


def can_run(request: Request, c: Computation) -> tuple[int, str] | None:
    """Why a new submission cannot start, with the HTTP status"""
    match c.last.Status:
        case Status.Running:
            return 409, "Previous computation is still running. This one has not been started. Please, try again later."
        case Status.Initializing:
            return 409, "The structure is still being initialized. This computation has not been started. Please, try again later."
        case Status.FailedInitialization:
            return 409, "The structure could not be loaded, no computation can run on it."
    if len(request.app.state.runner.jobs) >= request.app.state.settings.max_concurrent_computations:
        return 503, "Server is under heavy load, computation has not started. Please try again later"
    return None


class LoggedSubmission(APIRoute):
    """A submission (/Submit/Mole/{computation_id}) is logged before its body is read, so a refused one too"""

    def get_route_handler(self):
        handle, kind = super().get_route_handler(), self.path.split("/")[2]

        async def logged(request: Request):
            iplog.write(request.app.state.settings.working_directory, "Submit", request.client and request.client.host,
                        kind, request.path_params["computation_id"])
            return await handle(request)
        return logged


def errors(*codes: int) -> dict:
    """Error responses in the documentation"""
    return {code: {"model": Report} for code in (*codes, 500)}


UPLOAD_FORM = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary", "description": "*.pdb, *.cif, *.pdb.gz or *.cif.gz, at most 30 MB unpacked"}}}}}}}


class Submission(BaseModel):
    SubmitId: int
    Mode: str | None  # Mole, Pores; None: no parameters were submitted
    Status: Status
    ErrorMsg: str
    MoleConfig: dict  # mole.json, {} for pores
    PoresConfig: dict  # what the user submitted of pores.json, {} for tunnels


class CompInfo(BaseModel):
    ComputationId: str
    UserStructure: bool
    PdbId: str
    AssemblyId: str | None
    Submissions: list[Submission]  # those with a folder


PORES_PARAMETERS = "pores.json"  # the input of the pores tool, also what the user submitted
# a process of its own, as MOLE: a stop or the time limit kills it with MEMEMBED; the tests replace it
PORES_TOOL = [sys.executable, "-m", "mole_api.pores"]


def run_mole(settings: Settings, input: Path) -> Step:
    return Step([str(settings.mole), str(input)], errors_on_stderr=True)  # MOLE reports errors on stderr, with exit code 0


def submission(c: Computation, unit: computation.Unit) -> Submission:
    folder = c.submit_directory(unit.SubmitId)
    tunnels, pores = folder / mole.PARAMETERS, folder / PORES_PARAMETERS
    return Submission(SubmitId=unit.SubmitId, Mode="Mole" if tunnels.exists() else "Pores" if pores.exists() else None,
                      Status=unit.Status, ErrorMsg=unit.ErrorMsg,
                      MoleConfig=json.loads(tunnels.read_text()) if tunnels.exists() else {},
                      PoresConfig=PoresParameters.model_validate_json(pores.read_bytes()).user_parameters() if pores.exists() else {})


def pore_found(directory: Path) -> str | None:
    """Why a pore computation failed although MOLE did not"""
    results = directory / "json" / "data.json"
    if not results.exists():
        return "MOLE did not produce any result."
    if not json.loads(results.read_bytes()).get("Channels", {}).get("Paths"):
        return "No pore was found along the membrane normal."
    return None


class Version(BaseModel):
    APIVersion: str  # also of the pores tool, part of the API
    MoleVersion: str


async def create_from_upload(request: Request) -> Report | JSONResponse:
    """A computation of the uploaded structure, or why it is refused"""
    if int(request.headers.get("content-length") or 0) > upload.MAX_REQUEST_BYTES:  # not read at all
        return error(None, 413, upload.TOO_LARGE)
    settings: Settings = request.app.state.settings
    async with request.form() as form:
        file = form.get("file")
        if not isinstance(file, UploadFile) or not file.size:
            return error(None, 400, upload.NO_FILE)
        if (name := upload.stored_name(file.filename or "")) is None:
            return error(None, 400, upload.UNSUPPORTED)
        gzipped = (file.filename or "").lower().endswith(".gz")
        temp = settings.working_directory / f".upload-{uuid.uuid4().hex}"  # a computation only for a structure that fits
        try:
            if refused := await run_in_threadpool(upload.store, file.file, gzipped, temp):
                return error(None, *refused)
            c = computation.create(settings.working_directory, request.app.state.mole_version)
            c.StructureFile = name
            temp.rename(c.structure_path())
            c.save()
        finally:
            temp.unlink(missing_ok=True)
    request.app.state.runner.start(c, lambda: [run_mole(settings, mole.load_input(c))], init=True)
    return c.report()


def submit(request: Request, computation_id: str, parameters: MoleParameters | PoresRequest,
           write_input: Callable[[Computation, Path], list[Step]], finish: Callable[[Computation, Path], str | None]) -> Report | JSONResponse:
    """A new submission of the computation, run in the background, or why it is refused. write_input: writes its input
    into its folder, returns its processes; finish: after them, in a thread, returns why the run failed (see Runner)."""
    found = find(request, computation_id)
    if isinstance(found, JSONResponse):
        return found
    if problems := parameters.problems():
        return error(computation_id, 400, problems)
    if refused := can_run(request, found):
        return error(computation_id, *refused)

    found.add_submission(request.app.state.mole_version)
    directory = found.submit_directory(found.last.SubmitId)
    try:
        found.change_status(Status.Running)
        steps = write_input(found, directory)
    except Exception as e:  # e.g. the structure is missing
        found.change_status(Status.Error, str(e))
        return respond(found.report(), 500)
    report = found.report()  # before the run can change it
    request.app.state.runner.start(found, lambda: steps, init=False, finish=lambda: finish(found, directory))
    return report


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

    app = FastAPI(title="MOLEonline API", version=version("mole-api"), lifespan=lifespan)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=iplog.PROXIES)  # the client address, also for the log
    app.add_middleware(AspNetPaths)
    app.add_middleware(GZipMiddleware)  # for clients that accept it; not zips, data.json often has megabytes

    @app.exception_handler(Exception)
    async def unexpected(request: Request, e: Exception) -> JSONResponse:  # logged by uvicorn
        return error(None, 500, "An unexpected error occurred on the server. Please, try again later.")

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        """400 with a report, as the former C# API answered (not FastAPI's 422)"""
        details = "; ".join(f"{'.'.join(str(part) for part in error['loc'] if part != 'body')}: {error['msg']}" for error in e.errors())
        return error(request.path_params.get("computation_id"), 400, f"Input format error. {details}")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("docs")  # relative: also behind a proxy's prefix (/api/)

    @app.get("/Version")
    def get_version(request: Request) -> Version:
        """Versions of the API (with the pores tool) and of MOLE"""
        return Version(APIVersion=app.version, MoleVersion=request.app.state.mole_version)

    @app.get("/Status/{computation_id}", responses=errors(404, 410))
    def get_status(request: Request, computation_id: str, submitId: int = 0) -> Report:
        """State of a submission: -1 the last one, 0 (default) the first one, the load"""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        report = found.report(submitId)
        if not any(u.SubmitId == report.SubmitId for u in found.ComputationUnits):
            return respond(report, 404)  # "SubmitId N not found."
        return report

    @app.get("/CompInfo/{computation_id}", responses=errors(404, 410))
    def get_info(request: Request, computation_id: str) -> CompInfo:
        """The computation and its submissions with their parameters, so that the web can show them again"""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        return CompInfo(ComputationId=found.ComputationId, UserStructure=found.UserStructure, PdbId=found.PdbId, AssemblyId=found.AssemblyId,
                        Submissions=[submission(found, u) for u in found.ComputationUnits if found.submit_directory(u.SubmitId).is_dir()])

    @functools.cache
    def csa() -> CSA:
        return CSA()  # read on the first request (0.1 s), the tests start faster

    @app.get("/CSA/{computation_id}", responses=errors(404, 410))
    def get_csa(request: Request, computation_id: str) -> list[list[CSAResidue]]:
        """Active sites of the PDB entry from M-CSA (https://www.ebi.ac.uk/thornton-srv/m-csa/, CC BY 4.0), one per
        copy, as tunnel starting points; none for an uploaded structure"""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        return [] if found.UserStructure else csa().get(found.PdbId)

    @app.get("/Data/{computation_id}", responses={**errors(400, 404, 410), 200: {"description": "The file, as an attachment"}},
             response_class=Response)
    def get_data(request: Request, computation_id: str, submitId: int = -1, format: str = "json"):
        """A result of a submission (-1: the last one, 0: the load) to download. Formats: json (MOLE's data.json, what
        the web shows), report (zip of all), molecule (the structure), pymol, chimera, vmd (scripts), pdb (zip of the
        tunnels' profiles), membrane (membrane.json of a pore computation)."""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        last = found.last.SubmitId
        if submitId == -1:
            submitId = last
        if not 0 <= submitId <= last:
            return error(computation_id, 404, f"Submission {submitId} does not exist.", submitId)
        if format not in data.FORMATS:
            return error(computation_id, 400, f"Unknown format '{format}', the formats are {', '.join(data.FORMATS)}.", submitId)
        # MOLE's files appear while it runs, a failed run leaves some; the membrane is the computation's (pore computations)
        if format not in ("molecule", "membrane") and not found.has_results(submitId):
            return error(computation_id, 404, f"Submission {submitId} has no results, it has not finished or it failed.", submitId)
        if (file := data.download(found, submitId, format)) is None:
            return error(computation_id, 404, f"Data in format '{format}' are not available.", submitId)
        return file

    @app.post("/Init", responses=errors(400, 413), openapi_extra=UPLOAD_FORM)
    async def init_upload(request: Request) -> Report:
        """A computation of an uploaded structure, loaded by MOLE in the background (Initializing, then Initialized
        or FailedInitialization, see Status). A gzipped file is stored unpacked."""
        created = None
        try:
            response = await create_from_upload(request)
            if isinstance(response, Report):
                created = response.ComputationId
            return response
        finally:
            iplog.write(request.app.state.settings.working_directory, "Init", request.client and request.client.host, "Upload", created)

    @app.post("/Init/{pdb_id}", responses=errors(400))
    async def init_pdb(request: Request, pdb_id: str, assembly: str = pdbe.PREFERRED) -> Report:
        """A computation of a PDB entry, downloaded from PDBe and loaded by MOLE in the background (see Status).
        assembly: preferred (default): PDBe's preferred assembly, the one OPM has; asymmetric: the asymmetric unit;
        or an assembly id of the entry."""
        created = None
        settings: Settings = request.app.state.settings
        try:
            if not pdbe.PDB_ID.match(pdb_id):
                return error(None, 400, f"Invalid PDB id [{pdb_id}].")
            if not pdbe.ASSEMBLY.match(assembly):
                return error(None, 400, f"Invalid assembly [{assembly}]. Use {pdbe.PREFERRED}, {pdbe.ASYMMETRIC} or an assembly id of the entry.")
            c = computation.create(settings.working_directory, request.app.state.mole_version, pdb_id.lower())
            created = c.ComputationId
            request.app.state.runner.start(c, lambda: [run_mole(settings, mole.load_input(c))], init=True,
                                           prepare=lambda: pdbe.download(request.app.state.pdbe, c, assembly))
            return c.report()
        finally:
            iplog.write(settings.working_directory, "Init", request.client and request.client.host, "PDB", created)

    # on the event loop, as the runs: a stop and a run never interleave, nor two submissions
    submissions = APIRouter(prefix="/Submit", route_class=LoggedSubmission)

    @submissions.post("/Mole/{computation_id}", responses=errors(400, 404, 409, 410, 503))
    async def submit_mole(request: Request, computation_id: str, parameters: MoleParameters) -> Report:
        """Computes tunnels (pores with PoresAuto, PoresMerged or exits without an origin) in the loaded structure, in
        the background: Running, then Finished or Error (see Status with the SubmitId of the answer)"""
        settings: Settings = request.app.state.settings

        def write_input(c: Computation, directory: Path) -> list[Step]:
            path = mole.computation_input(c, parameters)
            (directory / mole.PARAMETERS).write_text(parameters.model_dump_json(indent=2))
            return [run_mole(settings, path)]
        return submit(request, computation_id, parameters, write_input, lambda c, directory: mole.zip_report(directory, c.structure_path()))

    @submissions.post("/Pores/{computation_id}", responses=errors(400, 404, 409, 410, 503))
    async def submit_pores(request: Request, computation_id: str, parameters: PoresRequest) -> Report:
        """Computes a pore across the membrane, in the background: the pores tool places the membrane (from OPM for
        the preferred assembly of a PDB entry, otherwise predicted by MEMEMBED) and writes MOLE's input of a path
        along its normal, MOLE computes the pore (see Status, membrane.json in Data)"""
        settings: Settings = request.app.state.settings

        def write_input(c: Computation, directory: Path) -> list[Step]:
            tool_input = directory / PORES_PARAMETERS
            tool_input.write_text(PoresParameters(
                Structure=str(c.structure_path()), PdbId=c.PdbId if c.DbModePores else None, WorkingDirectory=str(directory),
                Membrane=str(c.folder() / "membrane.json"), Memembed=str(settings.memembed), OpmCache=str(settings.opm_cache),
                InMembrane=parameters.InMembrane, IsBetaBarrel=parameters.IsBetaBarrel,
                Chains=parameters.Chains or [],
                ProbeRadius=DEFAULT_PORES_PROBE_RADIUS if parameters.ProbeRadius is None else parameters.ProbeRadius,
                InteriorThreshold=DEFAULT_PORES_INTERIOR_THRESHOLD if parameters.InteriorThreshold is None else parameters.InteriorThreshold,
            ).model_dump_json(indent=2))
            return [Step([*PORES_TOOL, str(tool_input)]), run_mole(settings, directory / mole.INPUT)]

        def finish(c: Computation, directory: Path) -> str | None:
            if problem := pore_found(directory):
                return problem
            mole.zip_report(directory, c.structure_path())
            return None
        return submit(request, computation_id, parameters, write_input, finish)

    app.include_router(submissions)

    # on the event loop, as the runs: a stop and a run never interleave
    @app.post("/Kill/{computation_id}", responses=errors(404, 409, 410))
    async def kill(request: Request, computation_id: str) -> Report:
        """Stops the computation's run, a download of its structure too: Aborted, or FailedInitialization for a load
        (nothing can run on it)"""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        if not request.app.state.runner.kill(computation_id):
            report = found.report()
            return error(computation_id, 409, f'Cannot kill computation with the status "{report.Status}"', report.SubmitId)
        if found.last.Status == Status.Initializing:
            found.change_status(Status.FailedInitialization, STOPPED_LOAD)
        else:
            found.change_status(Status.Aborted)
        return found.report()

    @app.delete("/Delete/{computation_id}", responses=errors(404, 410))
    async def delete(request: Request, computation_id: str) -> Report:
        """Stops the computation's run and marks it deleted (410 from then on); its files are kept"""
        found = find(request, computation_id)
        if isinstance(found, JSONResponse):
            return found
        request.app.state.runner.kill(computation_id)
        found.change_status(Status.Deleted)
        return found.report()

    return app


app = create_app()
