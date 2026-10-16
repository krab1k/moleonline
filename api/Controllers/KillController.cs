using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class KillController(ComputationManager manager) : Controller
{
    [HttpPost("{computationId}")]
    public ContentResult Post(string computationId)
    {
        var computation = manager.LoadComputation(computationId);
        if (computation == null) return Reports.NotFound(computationId);
        if (computation.IsDeleted()) return Reports.Gone(computationId);

        var (report, statusCode) = manager.KillComputation(computation);
        return Reports.Json(report, statusCode);
    }
}
