using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using System.Text.RegularExpressions;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public partial class InitController(ComputationManager manager) : Controller
{
    [GeneratedRegex(@"(\.pdb|\.cif|\.gz|\.pdb[0-9]+)$")]
    private static partial Regex StructureExtension();

    private const int MaxUploadMB = 30;

    [HttpPost("{id}")]
    public ContentResult Pdb(string id, string assemblyId)
    {
        var result = manager.CreatePDBComputation(id.ToLower(), assemblyId);
        return Content(result.ToJson(), "application/json");
    }


    [HttpPost("Pores/{id}", Name = "Init/Pores")]
    public ContentResult Pores(string id)
    {
        var result = manager.CreatePoreComputation(id.ToLower());
        return Content(result.ToJson(), "application/json");
    }



    [HttpPost]
    [Consumes("multipart/form-data")]
    [RequestSizeLimit(MaxUploadMB * 1024 * 1024)]
    public ContentResult Post(IFormFile file)
    {
        if (Request.ContentLength > MaxUploadMB * 1024 * 1024) // the file is then not read at all, it would look like no file
        {
            return Content(new ComputationReport()
            {
                ComputationId = null,
                ErrorMsg = $"The file is too large, at most {MaxUploadMB} MB can be uploaded. Compressed files (*.gz) are accepted.",
                Status = ComputationStatus.Error,
                SubmitId = 0
            }.ToJson(), "application/json");
        }
        if (file == null || file.Length == 0)
        {
            return Content(new ComputationReport()
            {
                ComputationId = null,
                ErrorMsg = "No structure file was uploaded or the file is empty.",
                Status = ComputationStatus.Error,
                SubmitId = 0
            }.ToJson(), "application/json");
        }
        if (!StructureExtension().IsMatch(file.FileName.ToLower()))
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
