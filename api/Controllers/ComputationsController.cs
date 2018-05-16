using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Newtonsoft.Json;
using System.IO;
using Mole.API.Utils;
using Mole.API.Models;

namespace Mole.API.Controllers
{
    public class ComputationsController : Controller
    {
        private readonly ComputationManager manager;
        private readonly Config config;


        public ComputationsController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
            config = manager.Config;
        }
    }
}