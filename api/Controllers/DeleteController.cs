using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class DeleteController(ComputationManager manager) : Controller
{
    [HttpDelete("{id}")]
    [HttpGet("{id}")] // old method, until the web uses DELETE
    public ContentResult Delete(string id)
    {
        var cpt = manager.LoadComputation(id);

        if (cpt == null) return Content(Computation.NotExists(id).ToJson(), "application/json");

        var result = manager.DeleteComputation(id);
        return Content(result.ToJson(), "application/json");
    }
}
