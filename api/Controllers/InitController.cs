using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Mole.API.Models;
using Newtonsoft.Json;
using Microsoft.AspNetCore.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace Mole.API.Controllers
{
    [Produces("application/json")]
    [Route("[controller]")]
    public class InitController : Controller
    {
        private readonly Config config;
        private ComputationManager manager;
        private string[] supportedExtensions = new string[] { ".pdb", ".cif", ".gz", ".pdbX" };

        public InitController(IOptions<Config> optionsAccessor)
        {
            config = optionsAccessor.Value;
            manager = new ComputationManager(config);
        }


        [HttpGet("{id}")]
        public string Get(string id, string assemblyId)
        {
            var result = manager.CreatePDBComputation(id, assemblyId);
            return result.ToJson();
        }


        [HttpPost]
        [Consumes("multipart/form-data")]
        public string Post(IFormFile file)
        {
            if (!Regex.Match(file.FileName.ToLower(), "(\\.pdb|\\.cif|\\.gz|\\.pdb[0-9]+)$").Success)
            {
                return JsonConvert.SerializeObject(new ComputationReport()
                {
                    ComputationId = null,
                    ErrorMsg = "Unsupported file type. Supported extensions are *.pdb, *.cif, *.pdbX (e.g. *.pdb1) and *.gz",
                    Status = ComputationStatus.Error,
                    SubmitId = 0
                }, Formatting.Indented);
            }
            var result = manager.CreateUserComputation(file);

            return result.ToJson();
        }




        [HttpGet("Pores/{id}")]
        public string Get(string id)
        {
            var result = manager.CreatePoreComputation(id);
            return result.ToJson();
        }

    }
}