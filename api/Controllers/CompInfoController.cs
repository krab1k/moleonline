using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class CompInfoController(ComputationManager manager) : Controller
{
    [HttpGet("{computationId}")]
    public ContentResult Get(string computationId) {
        return Content(manager.ComputationInfos(computationId), "application/json");
    }
}