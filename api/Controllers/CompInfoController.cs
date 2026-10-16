using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class CompInfoController(ComputationManager manager) : Controller
{
    [HttpGet("{computationId}")]
    public ContentResult Get(string computationId)
    {
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        return Content(manager.ComputationInfos(cpt), "application/json");
    }
}
