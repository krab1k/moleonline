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
        private readonly Config config;
        private ComputationManager manager;

        public DeleteController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }


        [HttpGet("{id}")]
        public string Get(string id)
        {
            var cpt = manager.LoadComputation(id);

            if (cpt == null) return Computation.NotExists(id).ToJson();

            var result = manager.DeleteComputation(id);
            return result.ToJson();
        }
    }
}