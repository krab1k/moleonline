using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class SubmitController(ComputationManager manager) : Controller
{
    [HttpPost("Mole/{computationId}", Name = "Submit/Mole")]
    public ContentResult Post(string computationId, [FromBody] ComputationParameters param)
    {
        LogIp();

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Content(Computation.NotExists(computationId).ToJson(), "application/json");
        if (param == null) return Content(new ComputationReport()
        {
            ComputationId = cpt.ComputationId,
            Status = ComputationStatus.Error,
            ErrorMsg = "Input format error. Request's body could not be serialized, please fix the error and try again."
        }.ToJson(), "application/json");


        var canRunMsg = manager.CanRun(cpt);

        if (!String.IsNullOrEmpty(canRunMsg))
        {
            return Content(
                new ComputationReport()
                {
                    ComputationId = computationId,
                    SubmitId = 0,
                    Status = ComputationStatus.Error,
                    ErrorMsg = canRunMsg
                }.ToJson(), "application/json");
        }

        cpt.AddCalculation();

        var report = manager.PrepareAndRunMole(cpt, param);

        return Content(report.ToJson(), "application/json");
    }



    [HttpPost("Pores/{computationId}", Name = "Submit/Pores")]
    public ContentResult Pores(string computationId, [FromBody] APIPoresParameters parameters)
    {
        LogIp();

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Content(Computation.NotExists(computationId).ToJson(), "application/json");
        if (parameters == null) return Content(new ComputationReport()
        {
            ComputationId = cpt.ComputationId,
            Status = ComputationStatus.Error,
            ErrorMsg = "Input format error. Request's body could not be serialized, please fix the error and try again."
        }.ToJson(), "application/json");

        var canRunMsg = manager.CanRun(cpt);

        if (!String.IsNullOrEmpty(canRunMsg))
        {
            return Content(
                new ComputationReport()
                {
                    ComputationId = computationId,
                    SubmitId = 0,
                    Status = ComputationStatus.Error,
                    ErrorMsg = canRunMsg
                }.ToJson(), "application/json");
        }

        cpt.AddCalculation();

        var result = manager.PrepareAndRunPores(cpt, parameters);

        return Content(result.ToJson(), "application/json");

    }


    private static readonly Lock ipLogLock = new(); // concurrent appends to the same file fail

    private void LogIp()
    {
        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} Submit - {Request.HttpContext.Connection.RemoteIpAddress}\n"; // read while the request is still alive
        var log = Path.Combine(manager.Config.WorkingDirectory, "Mole_api_IP_log.csv"); // next to the computations, it outlives the API
        Task.Run(() => { lock (ipLogLock) System.IO.File.AppendAllText(log, line); });
    }
}
