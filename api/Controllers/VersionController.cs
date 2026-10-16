using Microsoft.AspNetCore.Mvc;
using Newtonsoft.Json;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public class VersionController : Controller
{
    [HttpGet()]
    public ActionResult Get()
    {
        return Content(JsonConvert.SerializeObject(new Utils.Version()));
    }
}