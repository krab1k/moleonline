using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class CSAController : Controller
    {
        private readonly Config config;
        private ComputationManager manager;        

        public CSAController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }


        [HttpGet("{compId}")]
        public string Get(string compId)
        {
            var cpt = manager.LoadComputation(compId);

            if (cpt == null || cpt.UserStructure) return "[]";

            var residues = manager.GetActiveSite(cpt.PdbId);

            return JsonConvert.SerializeObject(residues, Formatting.Indented);
            }
    }
}
