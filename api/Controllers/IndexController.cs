using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Diagnostics;
using Mole.API.Models;
using Microsoft.Extensions.Options;
using System.IO;
using Mole.API.Utils;
using Newtonsoft.Json;
using Mole.API.ViewModels;

namespace Mole.API.Controllers
{
    public class IndexController : Controller
    {
        private readonly ComputationManager manager;
        private readonly Config config;


        public IndexController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
            config = manager.Config;
        }

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

        [Route("__computations")]
        public IActionResult Computations()
        {
            var data = Directory.GetDirectories(config.WorkingDirectory)
                            .Where(x => System.IO.File.Exists(Path.Combine(config.WorkingDirectory, x, MoleApiFiles.ComputationStatus)))
                            .Select(x =>
                            {
                                var file = Path.Combine(config.WorkingDirectory, x, MoleApiFiles.ComputationStatus);
                                var time = Directory.GetCreationTime(Path.Combine(config.WorkingDirectory, x));
                                var obj = JsonConvert.DeserializeObject<Computation>(System.IO.File.ReadAllText(file));
                                return (time: time, obj: obj);
                            })
                            .GroupBy(x => new DateTime(x.time.Year, x.time.Month, x.time.Day))
                            .OrderByDescending(x => x.Key)
                            .Select(x => new ComputationsSummary()
                            {
                                Date = $"{x.Key.Day}/{x.Key.Month}/{x.Key.Year}",
                                Count = x.Count(),
                                Summaries = x.Select(y => new Summary()
                                {
                                    Id = y.obj.ComputationId,
                                    Structure = y.obj.PdbId == string.Empty ? "UserStructure" : y.obj.PdbId,
                                    Computations = y.obj.ComputationUnits.Count
                                })
                            });

            ViewBag.Data = data;

            return View();
        }
    }
}