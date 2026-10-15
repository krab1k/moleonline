using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Mole.API.Models;
using Newtonsoft.Json;
using Microsoft.AspNetCore.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class InitController : Controller
    {
        private readonly ComputationManager manager;

        public InitController(IOptions<ComputationManager> optionsAccessor)
        {
            manager = optionsAccessor.Value;
        }


        [HttpGet("{id}")]
        public ContentResult Get(string id, string assemblyId)
        {
            var result = manager.CreatePDBComputation(id.ToLower(), assemblyId);
            return Content(result.ToJson(), "application/json");
        }


        [HttpGet("Pores/{id}", Name = "Init/Pores")]
        public ContentResult Get(string id)
        {
            var result = manager.CreatePoreComputation(id.ToLower());            
            return Content(result.ToJson(), "application/json");
        }



        [HttpPost]
        [Consumes("multipart/form-data")]
        public ContentResult Post(IFormFile file)
        {
            if (!Regex.Match(file.FileName.ToLower(), "(\\.pdb|\\.cif|\\.gz|\\.pdb[0-9]+)$").Success)
            {
                return Content(new ComputationReport()
                {
                    ComputationId = null,
                    ErrorMsg = "Unsupported file type. Supported extensions are *.pdb, *.cif, *.pdbX (e.g. *.pdb1) and *.gz",
                    Status = ComputationStatus.Error,
                    SubmitId = 0
                }.ToJson(), "application/json");
            }
            var result = manager.CreateUserComputation(file);

            return Content(result.ToJson(), "application/json");
        }






    }
}