using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Diagnostics;
using Mole.API.Models;

namespace Mole.API.Controllers;

public class IndexController : Controller
{
    public IActionResult Index()
    {
        Utils.Version v = new();

        ViewBag.PoresVersion = v.PoresVersion;
        ViewBag.MoleVersion = v.MoleVersion;
        ViewBag.APIVersion = v.APIVersion;
        ViewBag.Build = v.Build;

        return View();
    }


    public IActionResult Error()
    {
        var ex = HttpContext.Features.Get<IExceptionHandlerPathFeature>();

        if (ex != null)
        {
            var err = new string[] {
                $"Error: {DateTime.Now}",
                $"Path: {ex.Path}",
                $"Type: {ex.Error.GetType()}",
                $"Message: {ex.Error.Message}",
                $"Exception: {ex.Error}",
                $"========================================================="
            };

            System.IO.File.AppendAllLines("ErrorLog.log", err);
        }

        var report = new ComputationReport(null, 0, ComputationStatus.Error, "An unexpected error occurred on the server. Please, try again later.");
        return new ContentResult() { Content = report.ToJson(), ContentType = "application/json", StatusCode = 500 };
    }
}
