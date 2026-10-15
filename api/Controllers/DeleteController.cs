using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class DeleteController : Controller
    {
        private readonly ComputationManager manager;

        public DeleteController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }


        [HttpGet("{id}")]
        public ContentResult Get(string id)
        {
            var cpt = manager.LoadComputation(id);

            if (cpt == null) return Content(Computation.NotExists(id).ToJson(), "application/json");

            var result = manager.DeleteComputation(id);
            return Content(result.ToJson(), "application/json");
        }
    }
}