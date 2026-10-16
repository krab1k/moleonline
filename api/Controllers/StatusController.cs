using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class StatusController(ComputationManager manager) : Controller
{
    [HttpGet("{computationId}")]
    public ContentResult Get(string computationId, int submitId)
    {
        var cpt = manager.LoadComputation(computationId);

        if (cpt == null) return Content(Computation.NotExists(computationId).ToJson(), "application/json");

        return Content(cpt.GetComputationReport(submitId).ToJson(), "application/json");



    }
}