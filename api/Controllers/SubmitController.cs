using System;
using Mole.API;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Mole.API.Models;
using Newtonsoft.Json;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class SubmitController : Controller
    {
        private readonly Config config;
        private ComputationManager manager;


        public SubmitController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }




        [HttpPost("Mole/{computationId}")]
        public string Post(string computationId, [FromBody] ComputationParameters param)
        {
            Task.Run(() => LogIp($"Mole|{computationId}"));

            var cpt = manager.LoadComputation(computationId);
            if (cpt == null) return Computation.NotExists(computationId).ToJson();

            var canRun = manager.CanRun(cpt);

            if (!canRun.Item1)
            {
                return
                    new ComputationReport()
                    {
                        ComputationId = computationId,
                        SubmitId = 0,
                        Status = ComputationStatus.Error,
                        ErrorMsg = canRun.Item2
                    }.ToJson();
            }

            cpt.AddCalculation();

            if (param == null) param = new ComputationParameters();

            var report = manager.PrepareAndRunMole(cpt, param);

            return report.ToJson();
        }



        [HttpGet("Pores/{computationId}")]
        public string Get(string computationId, bool isBetaStructure, bool inMembraneMode, string chains)
        {
            Task.Run(() => LogIp($"Pores|{computationId}"));

            var cpt = manager.LoadComputation(computationId);
            if (cpt == null) return Computation.NotExists(computationId).ToJson();

            var canRun = manager.CanRun(cpt);

            if (!canRun.Item1)
            {
                return
                    new ComputationReport()
                    {
                        ComputationId = computationId,
                        SubmitId = 0,
                        Status = ComputationStatus.Error,
                        ErrorMsg = canRun.Item2
                    }.ToJson();
            }

            cpt.AddCalculation();

            var result = manager.PrepareAndRunPores(cpt, isBetaStructure, inMembraneMode, chains?.Split(new char[] { ',' }));

            return result.ToJson();

        }


        public void LogIp(string s)
        {
            System.IO.File.AppendAllText("Mole_api_IP_log.csv", $"{DateTime.Now} {Request.HttpContext.Connection.RemoteIpAddress.ToString()}\n");
        }


        private void CheckValidity()
        {

        }
    }
}