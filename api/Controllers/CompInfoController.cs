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
        private readonly Config config;
        private ComputationManager manager;

        public CompInfoController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }

        [HttpGet("{computationId}")]
        public string Get(string computationId) {
            return JsonConvert.SerializeObject(manager.ComputationInfos(computationId), Formatting.Indented);
        }
    }
}