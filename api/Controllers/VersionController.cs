using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class VersionController : Controller
    {
        private readonly ComputationManager manager;

        public VersionController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }

        [HttpGet()]
        public ActionResult Get()
        {
            return Content(JsonConvert.SerializeObject(new Utils.Version()));
        }
    }
}