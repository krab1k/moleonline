using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;
using System.IO;
using Mole.API.Utils;
using Mole.API.Models;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("__[controller]")]
    public class ComputationsController : Controller
    {
        private readonly ComputationManager manager;
        private readonly Config config;


        public ComputationsController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
            config = manager.Config;
        }

        public string Get()
        {
            return JsonConvert.SerializeObject(new
            {
                Sessions = Directory.GetDirectories(config.WorkingDirectory).Count(),
                SessionsWithoutComputation = Directory.GetDirectories(config.WorkingDirectory).Count(x => !System.IO.Directory.Exists(Path.Combine(x, "1"))),
                Computations =
                    new
                    {
                        MOLE = Directory.GetDirectories(config.WorkingDirectory)
                            .SelectMany(x => Directory.GetDirectories(x))
                            .Count(x => System.IO.File.Exists(Path.Combine(x, MoleApiFiles.MoleParams))),
                        Pores = Directory.GetDirectories(config.WorkingDirectory)
                            .SelectMany(x => Directory.GetDirectories(x))
                            .Count(x => System.IO.File.Exists(Path.Combine(x, MoleApiFiles.PoreParams))),
                    },
                PopularStructures = Directory.GetDirectories(config.WorkingDirectory)
                    .Select(x => Path.GetFileNameWithoutExtension(Directory.GetFiles(x).First(y => Path.GetExtension(y) != ".json")))
                    .GroupBy(x => x)
                    .OrderByDescending(x => x.Count())
                    .ToDictionary(x => x.Key, x => x.Count())
            }, Formatting.Indented);
        }
    }
}