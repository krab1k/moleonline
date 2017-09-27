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
        private readonly ComputationManager manager;        

        public CSAController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
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
