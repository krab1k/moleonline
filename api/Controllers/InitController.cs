using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;
using System.Text.RegularExpressions;

namespace Mole.API.Controllers;

[Produces("application/json")]
[Route("[controller]")]
public partial class InitController(ComputationManager manager) : Controller
{
    [GeneratedRegex(@"\.(pdb|cif)(\.gz)?$")] // one structure, PDB or mmCIF; MOLE and the web use its first model
    private static partial Regex StructureExtension();

    private const int MaxUploadMB = 30;

    [HttpPost("{id}")]
    public ContentResult Pdb(string id, string assemblyId)
    {
        return Reports.Json(manager.CreatePDBComputation(id.ToLower(), assemblyId)); // loads in the background, see Status
    }


    [HttpPost("Pores/{id}", Name = "Init/Pores")]
    public ContentResult Pores(string id)
    {
        return Reports.Json(manager.CreatePoreComputation(id.ToLower()));
    }



    [HttpPost]
    [Consumes("multipart/form-data")]
    [RequestSizeLimit(MaxUploadMB * 1024 * 1024)]
    public ContentResult Post(IFormFile file)
    {
        if (Request.ContentLength > MaxUploadMB * 1024 * 1024) // the file is then not read at all, it would look like no file
        {
            return Reports.Error(null, StatusCodes.Status413PayloadTooLarge, $"The file is too large, at most {MaxUploadMB} MB can be uploaded. Gzipped files (*.pdb.gz, *.cif.gz) are accepted.");
        }
        if (file == null || file.Length == 0)
        {
            return Reports.Error(null, StatusCodes.Status400BadRequest, "No structure file was uploaded or the file is empty.");
        }
        if (!StructureExtension().IsMatch(file.FileName.ToLower()))
        {
            return Reports.Error(null, StatusCodes.Status400BadRequest, "Unsupported file type. Supported are PDB (*.pdb) and mmCIF (*.cif) files, also gzipped (*.pdb.gz, *.cif.gz).");
        }
        return Reports.Json(manager.CreateUserComputation(file));
    }






}
