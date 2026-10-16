using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class KillController(ComputationManager manager) : Controller
    {
        [HttpGet("{computationId}")]
        public ContentResult Get(string computationId)
        {
            var computation = manager.LoadComputation(computationId);
            if (computation != null)
            {
                return Content(manager.KillComputation(computation).ToJson(), "application/json");
            }

            return Content(Computation.NotExists(computationId).ToJson(), "application/json");
        }
    }
}