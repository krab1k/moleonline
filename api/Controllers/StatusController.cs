using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class StatusController : Controller
    {
        private readonly Config config;
        private ComputationManager manager;

        public StatusController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }

        [HttpGet("{computationId}")]
        public string Get(string computationId, int submitId)
        {
            var cpt = manager.LoadComputation(computationId);

            if (cpt == null) return Computation.NotExists(computationId).ToJson();

            return cpt.GetComputationReport(submitId).ToJson();



        }
    }
}