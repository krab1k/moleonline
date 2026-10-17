using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using System.Text.RegularExpressions;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public partial class SubmitController(ComputationManager manager) : Controller
{
    [GeneratedRegex("[^A-Za-z0-9]")]
    private static partial Regex LoggedId();

    [HttpPost("Mole/{computationId}", Name = "Submit/Mole")]
    public ContentResult Post(string computationId, [FromBody] ComputationParameters param)
    {
        LogIp("Mole", computationId);

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        if (param == null) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, "Input format error. Request's body could not be serialized, please fix the error and try again.");
        if (param.Validate() is string invalid) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, invalid);
        if (manager.CanRun(cpt) is (int statusCode, string reason)) return Reports.Error(cpt.ComputationId, statusCode, reason);

        cpt.AddCalculation();

        var (report, started) = manager.PrepareAndRunMole(cpt, param);
        return Reports.Json(report, started ? StatusCodes.Status200OK : StatusCodes.Status500InternalServerError);
    }



    [HttpPost("Pores/{computationId}", Name = "Submit/Pores")]
    public ContentResult Pores(string computationId, [FromBody] PoresRequest parameters)
    {
        LogIp("Pores", computationId);

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        if (parameters == null) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, "Input format error. Request's body could not be serialized, please fix the error and try again.");
        if (parameters.Validate() is string invalid) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, invalid);
        if (manager.CanRun(cpt) is (int statusCode, string reason)) return Reports.Error(cpt.ComputationId, statusCode, reason);

        cpt.AddCalculation();

        var (report, started) = manager.PrepareAndRunPores(cpt, parameters);
        return Reports.Json(report, started ? StatusCodes.Status200OK : StatusCodes.Status500InternalServerError);
    }


    private static readonly Lock ipLogLock = new(); // concurrent appends to the same file fail

    /// <summary>One line per submission request: time, client address, Mole or Pores, computation id (also refused requests)</summary>
    private void LogIp(string kind, string computationId)
    {
        var id = LoggedId().Replace(computationId.Length > 40 ? computationId[..40] : computationId, "?"); // ids are letters and digits; any id is logged, one request stays one line
        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} Submit - {Request.HttpContext.Connection.RemoteIpAddress} {kind} {id}\n"; // read while the request is still alive
        var log = Path.Combine(manager.Config.WorkingDirectory, "Mole_api_IP_log.csv"); // next to the computations, it outlives the API
        Task.Run(() => { lock (ipLogLock) System.IO.File.AppendAllText(log, line); });
    }
}
