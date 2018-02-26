using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Diagnostics;

namespace Mole.API.Controllers
{
    public class IndexController : Controller
    {
        public IActionResult Index()
        {
            Utils.Version v = new Utils.Version();

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
                    $"Exception: {ex.Error.ToString()}",
                    $"========================================================="
                };

                System.IO.File.AppendAllLines("ErrorLog.log", err);
            }

            return View();
        }
    }
}