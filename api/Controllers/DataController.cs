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
        var cpt = manager.LoadComputation(computationId);
        if (cpt == null) return Reports.NotFound(computationId);
        if (cpt.IsDeleted()) return Reports.Gone(computationId);

        var c = cpt.GetComputationReport();
        if (submitId < -1 || submitId > c.SubmitId) return Reports.Error(computationId, StatusCodes.Status404NotFound, $"Submission {submitId} does not exist.", submitId);
        if (!ComputationManager.DataFormats.Contains(format))
            return Reports.Error(computationId, StatusCodes.Status400BadRequest, $"Unknown format '{format}', the formats are {string.Join(", ", ComputationManager.DataFormats)}.", submitId);

        try
        {
            (Stream, string) data = manager.QueryFile(computationId, submitId, format); // the result disposes the stream

            if (System.IO.Path.GetExtension(data.Item2) == ".json") return File(data.Item1, "application/json", data.Item2);
            if (System.IO.Path.GetExtension(data.Item2) == ".zip") return File(data.Item1, "application/zip", data.Item2);

            return File(data.Item1, MediaTypeNames.Text.Plain, data.Item2);
        }
        catch (Exception e) when (e is FileNotFoundException or DirectoryNotFoundException or InvalidOperationException)
        {
            // no results (yet): a running or failed submission, no membrane outside pore computations
            return Reports.Error(computationId, StatusCodes.Status404NotFound, $"Data in format '{format}' are not available.", submitId);
        }
    }
}
