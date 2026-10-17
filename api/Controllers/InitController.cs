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

    private const int MaxRequestMB = Computation.MaxStructureMB + 1; // the file and the form around it; the file is checked when saved

    [GeneratedRegex(@"^[A-Za-z0-9]{1,10}$")] // goes into PDBe's URL
    private static partial Regex AssemblyName();

    /// <param name="assembly">preferred (default): PDBe's preferred assembly, asymmetric: the asymmetric unit, or an assembly id</param>
    [HttpPost("{id}")]
    public ContentResult Pdb(string id, string assembly = Computation.PreferredAssembly)
    {
        if (!AssemblyName().IsMatch(assembly))
        {
            return Reports.Error(null, StatusCodes.Status400BadRequest, $"Invalid assembly [{assembly}]. Use {Computation.PreferredAssembly}, {Computation.AsymmetricUnit} or an assembly id of the entry.");
        }
        return Reports.Json(manager.CreatePDBComputation(id.ToLower(), assembly)); // loads in the background, see Status
    }

    [HttpPost]
    [Consumes("multipart/form-data")]
    [RequestSizeLimit(MaxRequestMB * 1024 * 1024)]
    public ContentResult Post(IFormFile file)
    {
        if (Request.ContentLength > MaxRequestMB * 1024 * 1024) // the file is then not read at all, it would look like no file
        {
            return Reports.Error(null, StatusCodes.Status413PayloadTooLarge, $"The file is too large, at most {Computation.MaxStructureMB} MB can be uploaded. Gzipped files (*.pdb.gz, *.cif.gz) are accepted.");
        }
        if (file == null || file.Length == 0)
        {
            return Reports.Error(null, StatusCodes.Status400BadRequest, "No structure file was uploaded or the file is empty.");
        }
        if (!StructureExtension().IsMatch(file.FileName.ToLower()))
        {
            return Reports.Error(null, StatusCodes.Status400BadRequest, "Unsupported file type. Supported are PDB (*.pdb) and mmCIF (*.cif) files, also gzipped (*.pdb.gz, *.cif.gz).");
        }
        var (report, status) = manager.CreateUserComputation(file);
        return Reports.Json(report, status);
    }






}
