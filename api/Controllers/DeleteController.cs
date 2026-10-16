using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class DeleteController(ComputationManager manager) : Controller
{
    [HttpDelete("{id}")]
    public ContentResult Delete(string id)
    {
        var cpt = manager.LoadComputation(id);
        if (cpt == null) return Reports.NotFound(id);

        return Reports.Json(manager.DeleteComputation(id));
    }
}
