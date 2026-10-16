using Microsoft.AspNetCore.Mvc;
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
        // the exception handler middleware has logged the exception
        var report = new ComputationReport(null, 0, ComputationStatus.Error, "An unexpected error occurred on the server. Please, try again later.");
        return new ContentResult() { Content = report.ToJson(), ContentType = "application/json", StatusCode = 500 };
    }
}
