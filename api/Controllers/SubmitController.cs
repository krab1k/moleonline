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
        private readonly ComputationManager manager;


        public SubmitController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }




        [HttpPost("Mole/{computationId}", Name = "Submit/Mole")]
        public ContentResult Post(string computationId, [FromBody] ComputationParameters param)
        {
            Task.Run(() => LogIp());

            var cpt = manager.LoadComputation(computationId);
            if (cpt == null) return Content(Computation.NotExists(computationId).ToJson(), "application/json");
            if (param == null) return Content(new ComputationReport()
            {
                ComputationId = cpt.ComputationId,
                Status = ComputationStatus.Error,
                ErrorMsg = "Input format error. Request's body could not be serialized, please fix the error and try again."
            }.ToJson(), "application/json");


            var canRunMsg = manager.CanRun(cpt);

            if (!String.IsNullOrEmpty(canRunMsg))
            {
                return Content(
                    new ComputationReport()
                    {
                        ComputationId = computationId,
                        SubmitId = 0,
                        Status = ComputationStatus.Error,
                        ErrorMsg = canRunMsg
                    }.ToJson(), "application/json");
            }

            cpt.AddCalculation();

            var report = manager.PrepareAndRunMole(cpt, param);

            return Content(report.ToJson(), "application/json");
        }



        [HttpPost("Pores/{computationId}", Name = "Submit/Pores")]
        public ContentResult Pores(string computationId, [FromBody] APIPoresParameters parameters)
        {
            Task.Run(() => LogIp());

            var cpt = manager.LoadComputation(computationId);
            if (cpt == null) return Content(Computation.NotExists(computationId).ToJson(), "application/json");

            var canRunMsg = manager.CanRun(cpt);

            if (!String.IsNullOrEmpty(canRunMsg))
            {
                return Content(
                    new ComputationReport()
                    {
                        ComputationId = computationId,
                        SubmitId = 0,
                        Status = ComputationStatus.Error,
                        ErrorMsg = canRunMsg
                    }.ToJson(), "application/json");
            }

            cpt.AddCalculation();

            var result = manager.PrepareAndRunPores(cpt, parameters);

            return Content(result.ToJson(), "application/json");

        }


        private void LogIp()
        {
            System.IO.File.AppendAllText("Mole_api_IP_log.csv", $"{DateTime.Now} Submit - {Request.HttpContext.Connection.RemoteIpAddress.ToString()}\n");
        }
    }
}