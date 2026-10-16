using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Newtonsoft.Json;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class CSAController(ComputationManager manager) : Controller
{
    [HttpGet("{compId}")]
    public ContentResult Get(string compId)
    {
        var cpt = manager.LoadComputation(compId);
        if (cpt == null) return Reports.NotFound(compId);
        if (cpt.UserStructure) return Content("[]", "application/json"); // CSA knows PDB entries only

        var residues = manager.GetActiveSite(cpt.PdbId);

        return Content(JsonConvert.SerializeObject(residues, Formatting.Indented), "application/json");
    }
}
