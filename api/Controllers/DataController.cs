using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using System.Net.Mime;

namespace Mole.API.Controllers;

[Route("[controller]")]
public class DataController(ComputationManager manager) : Controller
{
    [HttpGet("{computationId}")]
    public ActionResult Get(string computationId, int submitId = -1, string format = "json")
    {

        var c = manager.GetComputationReport(computationId);

        if (c.ComputationId == null) return StatusCode(404);
        if (submitId < -1 || submitId > c.SubmitId) return StatusCode(404);
        if (c.Status == ComputationStatus.Deleted) return StatusCode(410);


        try
        {
            (byte[], string) data = manager.QueryFile(computationId, submitId, format);

            if (System.IO.Path.GetExtension(data.Item2) == ".json") return File(data.Item1, "application/json", data.Item2);
            if (System.IO.Path.GetExtension(data.Item2) == ".zip") return File(data.Item1, "application/zip", data.Item2);

            return File(data.Item1, MediaTypeNames.Text.Plain, data.Item2);
        }
        catch (Exception) {
            // 200 with a JSON body, the web loads the data of all submissions and skips those without channels
            return Content(new ComputationReport(computationId, submitId, ComputationStatus.Error, $"Data in format '{format}' are not available.").ToJson(), "application/json");
        }
    }
}