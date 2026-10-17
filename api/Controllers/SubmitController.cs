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
        IpLog.Write(this, manager.Config.WorkingDirectory, "Submit", "Mole", computationId);

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        if (param == null) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, "Input format error. Request's body could not be serialized, please fix the error and try again.");
        if (param.Validate() is string invalid) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, invalid);
        if (manager.CanRun(cpt) is (int statusCode, string reason)) return Reports.Error(cpt.ComputationId, statusCode, reason);

        cpt.AddCalculation(manager.MoleVersion);

        var (report, started) = manager.PrepareAndRunMole(cpt, param);
        return Reports.Json(report, started ? StatusCodes.Status200OK : StatusCodes.Status500InternalServerError);
    }



    [HttpPost("Pores/{computationId}", Name = "Submit/Pores")]
    public ContentResult Pores(string computationId, [FromBody] PoresRequest parameters)
    {
        IpLog.Write(this, manager.Config.WorkingDirectory, "Submit", "Pores", computationId);

        using var submitting = manager.SubmitLock.EnterScope();
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        if (parameters == null) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, "Input format error. Request's body could not be serialized, please fix the error and try again.");
        if (parameters.Validate() is string invalid) return Reports.Error(cpt.ComputationId, StatusCodes.Status400BadRequest, invalid);
        if (manager.CanRun(cpt) is (int statusCode, string reason)) return Reports.Error(cpt.ComputationId, statusCode, reason);

        cpt.AddCalculation(manager.MoleVersion);

        var (report, started) = manager.PrepareAndRunPores(cpt, parameters);
        return Reports.Json(report, started ? StatusCodes.Status200OK : StatusCodes.Status500InternalServerError);
    }
}
