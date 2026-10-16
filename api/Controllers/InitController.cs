using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using System.Text.RegularExpressions;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class InitController(ComputationManager manager) : Controller
    {
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