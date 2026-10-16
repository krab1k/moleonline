using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class EBIController(ComputationManager manager) : Controller
    {
        [HttpGet("{pdbId}")]
        public ContentResult Get(string pdbId, bool ignoreHet)
        {
            var para = new ComputationParameters()
            {
                Input = new Input(),
                Cavity = new Cavity()
                {
                    IgnoreHETAtoms = ignoreHet,
                },
                Tunnel = new Tunnel()
            };
            var result = manager.InitAndRunEBI(pdbId, para);

            return Content(result.ToJson(), "application/json");

        }
    }
}