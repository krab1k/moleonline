using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class VersionController(Utils.MoleVersion moleVersion) : Controller
{
    [HttpGet()]
    public ActionResult Get()
    {
        return Content(JsonConvert.SerializeObject(new Utils.Version { MoleVersion = moleVersion.Value }), "application/json");
    }
}
