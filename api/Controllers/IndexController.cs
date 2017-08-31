using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace Mole.API.Controllers
{
    public class IndexController : Controller
    {
        public IActionResult Index()
        {
            ViewBag.PoresVersion = "1.4.4";
            ViewBag.MoleVersion = "2.5.17.4.24";
            ViewBag.APIVersion = "0.3";

            return View();
        }
    }
}