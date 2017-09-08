using System;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Mole.API.Models;
using System.Net.Mime;

namespace Mole.API.Controllers
{
    [Route("[controller]")]
    public class DataController : Controller
    {
        private readonly Config config;
        private ComputationManager manager;

        public DataController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }

        [HttpGet("{computationId}")]
        public ActionResult Get(string computationId, int submitId = 0, string format = "json")
        {

            var c = manager.GetComputationReport(computationId);

            if (c.ComputationId == null) return StatusCode(404);
            if (submitId < 0 || submitId > c.SubmitId) return StatusCode(404);
            if (c.Status == ComputationStatus.Deleted) return StatusCode(410);
            

            try
            {
                (byte[], string) data = manager.QueryFile(computationId, submitId, format);

                if (System.IO.Path.GetExtension(data.Item2) == ".json") return File(data.Item1, "application/json", data.Item2);
                if (System.IO.Path.GetExtension(data.Item2) == ".zip") return File(data.Item1, "application/zip", data.Item2);

                return File(data.Item1, MediaTypeNames.Text.Plain, data.Item2);
            }
            catch (Exception) {
                return StatusCode(500);
            }
        }
    }
}