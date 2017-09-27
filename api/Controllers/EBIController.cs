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
    public class EBIController : Controller
    {
        private readonly ComputationManager manager;

        public EBIController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;

        }

        [HttpGet("{pdbId}")]
        public string Get(string pdbId, bool ignoreHet)
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

            return result.ToJson();

        }
    }
}