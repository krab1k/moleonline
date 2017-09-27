using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class CompInfoController : Controller
    {
        private readonly ComputationManager manager;

        public CompInfoController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }

        [HttpGet("{computationId}")]
        public string Get(string computationId) {
            return manager.ComputationInfos(computationId);
        }
    }
}