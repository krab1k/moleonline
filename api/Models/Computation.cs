using Mole.API.Utils;
using Newtonsoft.Json;
using System.Net;
using System.Text.RegularExpressions;
using System.Xml.Linq;

namespace Mole.API.Models;

/// <summary>
/// Represents a single computation created with submission controller
/// </summary>
public class ComputationUnit
{
    public int SubmitId { get; set; }
    public string Status { get; set; } = "";
    public string ErrorMsg { get; set; } = "";

    public static ComputationUnit Initialize()
    {
        return new ComputationUnit()
        {
            SubmitId = 1,
            Status = ComputationStatus.Initializing,
            ErrorMsg = String.Empty
        };
    }

    public void ChangeStatus(string status, string error)
    {
        Status = status;
        ErrorMsg = error;
    }
}


/// <summary>
/// Report of the current computation status returned to the user
/// </summary>
public class ComputationReport
{
    public string? ComputationId { get; set; } // null when no computation was created (a refused upload)
    public int SubmitId { get; set; }
    public string Status { get; set; }
    public string ErrorMsg { get; set; }


    public ComputationReport(string? computationId, int id, string status, string errorMsg)
    {
        ComputationId = computationId;
        SubmitId = id;
        Status = status;
        ErrorMsg = errorMsg;
    }



    public ComputationReport(string computationId, ComputationUnit unit)
    {
        this.ComputationId = computationId;
        this.SubmitId = unit.SubmitId;
        this.Status = unit.Status;
        this.ErrorMsg = unit.ErrorMsg;
    }

    internal string ToJson()
    {
        return JsonConvert.SerializeObject(this, Formatting.Indented);
    }
}



public partial class Computation
{
    [GeneratedRegex("[/+=]")]
    private static partial Regex Base64Symbols();

    public string ComputationId { get; set; } = "";
    public string BaseDir { get; set; } = "";
    public bool UserStructure { get; set; }
    public string PdbId { get; set; } = ""; // empty for uploaded structures
    public string? AssemblyId { get; set; } // null: the full structure
    public bool DbModePores { get; set; }
    /// <summary>
    /// File name of the structure in the computation folder, set once the file is complete (downloaded or uploaded)
    /// </summary>
    public string? StructureFile { get; set; }
    public List<ComputationUnit> ComputationUnits { get; set; } = [];

    /// <summary>
    /// API process that wrote the status last. Running/Initializing written by another process was interrupted by a restart.
    /// </summary>
    public string? ApiInstance { get; set; }

    public static readonly string CurrentApiInstance = Guid.NewGuid().ToString("N");



    public Computation() { }


    /// <summary>
    /// Initialize new computation. Status Initializing, submission Id = 1.
    /// </summary>
    /// <param name="baseDir">Working directory of the API</param>
    /// <param name="pdbId">PDB id to be downloaded</param>
    /// <param name="bioUnit">Optional biological assembly to be downloaded</param>
    public Computation(string baseDir, string pdbId = "", string? bioUnit = "")
    {
        ComputationId = Base64Symbols().Replace(Convert.ToBase64String(Guid.NewGuid().ToByteArray()), "");
        ComputationUnits = [ComputationUnit.Initialize()];
        UserStructure = pdbId == string.Empty;
        PdbId = pdbId;
        AssemblyId = bioUnit;
        BaseDir = baseDir;

        Directory.CreateDirectory(Path.Combine(baseDir, ComputationId));

        SaveStatus();
    }







    /// <summary>
    /// Given submit Id a computation report specifying computation status is retrieved. If no id is provided, than the last submission is retrieved
    /// </summary>
    /// <param name="i">id of submission</param>
    /// <returns>Computation report of given submission Id</returns>
    internal ComputationReport GetComputationReport(int i = -1)
    {
        if (i == -1) i = this.ComputationUnits.Count;
        if (i == 0) i = 1;
        var unit = ComputationUnits.FirstOrDefault(x => x.SubmitId == i);

        if (unit == null)
            return new ComputationReport(ComputationId, i, ComputationStatus.Error, $"SubmitId {i} not found.");
        else
            return new ComputationReport(this.ComputationId, unit);
    }




    /// <summary>
    /// Change status of present computation to a given value as long as it is not deleted.
    /// </summary>
    /// <param name="status"></param>
    /// <param name="error"></param>
    public void ChangeStatus(string status, string error = "")
    {
        if (IsDeleted()) return;

        ComputationUnits.Last().ChangeStatus(status, error);
        SaveStatus();
    }


    /// <summary>
    /// Adds a new submission to the existing computation. That includes:
    ///    - create new submission directory
    ///    - increment submission id.
    ///
    /// </summary>
    public void AddCalculation()
    {
        if (this.ComputationUnits.Last().Status != ComputationStatus.Initialized)
        {
            var unit = new ComputationUnit()
            {
                Status = ComputationStatus.Initialized,
                SubmitId = this.ComputationUnits.Count + 1,
                ErrorMsg = string.Empty,
            };

            ComputationUnits.Add(unit);
        }
        Directory.CreateDirectory(Path.Combine(BaseDir, ComputationId, ComputationUnits.Last().SubmitId.ToString()));
        SaveStatus();
    }



    /// <summary>
    /// Shorthand for creating a non existing computation with given Id.
    /// </summary>
    /// <param name="computationId"></param>
    /// <returns>Empty computation</returns>
    public static ComputationReport NotExists(string computationId) =>
         new(computationId, 0, ComputationStatus.Error, $"ComputationId [{computationId}] does not exists.\n");


    public async Task DownloadBioStructureAsync()
    {
        AssemblyId = await GetBioAssemblyIdAsync();
        await DownloadStructureAsync(isBioAssembly: true);
    }


    /// <summary>
    /// Downloads Protein Data Bank file from Model Server https://www.ebi.ac.uk/pdbe/model-server
    /// </summary>
    public async Task DownloadStructureAsync(bool isBioAssembly = false)
    {
        var url = AssemblyId != null ? $"https://www.ebi.ac.uk/pdbe/model-server/v1/{PdbId}/assembly?name={AssemblyId}" : $"https://www.ebi.ac.uk/pdbe/model-server/v1/{PdbId}/full";

        var file = Path.Combine(BaseDir, ComputationId, PdbId + ".cif");
        var temp = file + ".download"; // the structure file appears only when complete
        try
        {
            HttpStatusCode status;
            await using (var stream = File.Create(temp))
            {
                status = await DownloadAsync(url, stream);
            }

            if (status == HttpStatusCode.NotFound)
            {
                ChangeStatus(ComputationStatus.FailedInitialization, $"Structure [{PdbId}] is unlikely to exist or has been made obsolete.");
                return;
            }
            if (status != HttpStatusCode.OK)
            {
                ChangeStatus(ComputationStatus.FailedInitialization, $"PDBe returned an error ({(int)status}) when downloading structure [{PdbId}]. Please, try again later.");
                return;
            }

            File.Move(temp, file);
            StructureFile = Path.GetFileName(file);
            var error = CheckDownloadedStructure(file);
            if (!String.IsNullOrEmpty(error))
            {
                ChangeStatus(ComputationStatus.FailedInitialization, error);
                return;
            }
            // the preferred assembly, the one OPM has; unknown when the lookup failed, then the full structure was downloaded
            DbModePores = AssemblyId != null && (isBioAssembly || AssemblyId == await GetBioAssemblyIdAsync());
            SaveStatus();
        }
        catch (Exception e) when (e is HttpRequestException || e is OperationCanceledException || e is IOException)
        {
            ChangeStatus(ComputationStatus.FailedInitialization, "The structure could not be downloaded, PDBe (www.ebi.ac.uk) is not available at the moment. Please, try again later.");
        }
        catch (Exception e)
        {
            ChangeStatus(ComputationStatus.FailedInitialization, $"Structure [{PdbId}] could not be downloaded: {e.Message}");
        }
        finally
        {
            if (File.Exists(temp)) File.Delete(temp);
        }
    }

    public async Task<string?> GetBioAssemblyIdAsync()
    {
        try
        {
            using var stream = new MemoryStream();
            if (await DownloadAsync($"https://www.ebi.ac.uk/pdbe/static/entry/download/{PdbId}-assembly.xml", stream) != HttpStatusCode.OK) return null;
            stream.Position = 0;
            return XDocument.Load(stream).Root?.Elements("assembly").FirstOrDefault(x => (string?)x.Attribute("prefered") == "True")?.Attribute("id")?.Value;
        }
        catch (Exception)
        { return null; }
    }


    private static readonly HttpClient http = new(new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(15) })
    {
        Timeout = Timeout.InfiniteTimeSpan // handled in Download
    };
    private static readonly TimeSpan ResponseTimeout = TimeSpan.FromSeconds(120); // model server builds assemblies on the fly
    private static readonly TimeSpan IdleTimeout = TimeSpan.FromSeconds(60);


    /// <summary>
    /// Downloads url into target. Fails when the response does not start within ResponseTimeout
    /// or no data arrive within IdleTimeout, so that large structures can still be downloaded on slow links.
    /// </summary>
    /// <returns>HTTP status code of the response, target is written only for OK</returns>
    private static async Task<HttpStatusCode> DownloadAsync(string url, Stream target)
    {
        using var cts = new CancellationTokenSource(ResponseTimeout);
        using var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cts.Token);
        if (response.StatusCode != HttpStatusCode.OK) return response.StatusCode;

        await using var body = await response.Content.ReadAsStreamAsync(cts.Token);
        var buffer = new byte[81920];
        while (true)
        {
            cts.CancelAfter(IdleTimeout);
            var read = await body.ReadAsync(buffer, cts.Token);
            if (read == 0) break;
            await target.WriteAsync(buffer.AsMemory(0, read), cts.Token);
        }
        return HttpStatusCode.OK;
    }



    /// <summary>
    /// Checks if downloaded structure is correct or an error message has been retrieved
    /// </summary>
    /// <param name="path">Path with the downloaded protein structure</param>
    /// <returns>Error message of the query, string.empty otherwise()</returns>
    private string CheckDownloadedStructure(string path)
    {
        var errorLine = File.ReadAllLines(path).FirstOrDefault(x => x.StartsWith("_coordinate_server_error.message"));

        if (errorLine == null) return string.Empty;
        else
        {
            var temp = errorLine[36..].Trim();
            return temp[1..^1];
        }
    }



    /// <summary>
    /// Shorthand for SubmitDirectoryPath
    /// </summary>
    /// <param name="submitId"></param>
    /// <returns>Complete path to the submit directory folder</returns>
    /// <summary>
    /// Deleting a computation marks its last submission; the computation as a whole is gone
    /// </summary>
    public bool IsDeleted() => ComputationUnits.Last().Status == ComputationStatus.Deleted;

    public string StructurePath() => StructureFile != null
        ? Path.Combine(BaseDir, ComputationId, StructureFile)
        : throw new InvalidOperationException($"Computation {ComputationId} has no structure.");

    public string SubmitDirectory(int submitId = -1) => Path.Combine(BaseDir, ComputationId, submitId == -1 ? this.ComputationUnits.Last().SubmitId.ToString() : submitId.ToString());



    /// <summary>
    /// Returns path to the ComputationStatus file
    /// </summary>
    /// <param name="baseDir"></param>
    /// <returns></returns>
    private string StatusPath() => Path.Combine(BaseDir, ComputationId, MoleApiFiles.ComputationStatus);


    /// <summary>
    /// Saves current state of the computation to the ComputationStatus file
    /// </summary>
    /// <param name="baseDir"></param>
    public void SaveStatus()
    {
        // write a temporary file first and rename it, so that concurrent readers (status polling) never see a partially written file
        ApiInstance = CurrentApiInstance;
        var temp = $"{StatusPath()}.{Guid.NewGuid():N}.tmp.json";
        File.WriteAllText(temp, JsonConvert.SerializeObject(this, Formatting.Indented));
        File.Move(temp, StatusPath(), overwrite: true);
    }

}
