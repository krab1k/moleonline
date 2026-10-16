using Mole.API.Utils;
using Newtonsoft.Json;
using System.Diagnostics;
using System.Xml.Linq;
using System.Text;
using Microsoft.Extensions.Options;
using System.Collections.Concurrent;

namespace Mole.API.Models;

/// <summary>
/// API logic
/// </summary>
public class ComputationManager(IOptions<Config> options)
{
    public Config Config { get; } = options.Value;
    private readonly CSA csa = new(options.Value.CSA);
    private readonly ConcurrentDictionary<string, RunningJob> RunningProcesses = new(); // runs waiting for a slot and running
    private readonly SemaphoreSlim slots = new(Math.Max(1, options.Value.MaxConcurentComputations)); // MaxConcurentComputations processes at once, initializations included

    /// <summary>
    /// One run of MOLE/Pores. Submissions of a computation share its id, so the kill flag must belong to the run.
    /// Process is null while the run waits for a slot. Lock the job to start or kill its process.
    /// </summary>
    private class RunningJob
    {
        public Process Process;
        public volatile bool Killed;
    }



    #region Methods
    /// <summary>
    /// Creates a MOLE computation with a given PDB id
    /// </summary>
    /// <param name="id">PDB id</param>
    /// <param name="bioUnit">optional biologicall assembly</param>
    /// <returns>Computation report object</returns>
    public ComputationReport CreatePDBComputation(string id, string assemblyId)
    {
        var computation = new Computation(Config.WorkingDirectory, id, assemblyId);

        Task.Run(() => computation.DownloadStructure()).ContinueWith(x => Init(computation));

        return computation.GetComputationReport(1);
    }


    /// <summary>
    /// Creates a Pore computation with a given PDB id biological assembly is implicitly downloaded.
    /// </summary>
    /// <param name="id">PDB id</param>
    /// <param name="bioUnit">optional biologicall assembly</param>
    /// <returns>Computation report object</returns>
    internal ComputationReport CreatePoreComputation(string id)
    {
        var computation = new Computation(Config.WorkingDirectory, id);
        Task.Run(() => computation.DownloadBioStructure()).ContinueWith(x => Init(computation));


        return computation.GetComputationReport(1);

    }



    /// <summary>
    /// Handles user computations with user file provided
    /// </summary>
    /// <param name="file"></param>
    /// <returns>Computation report object</returns>
    public ComputationReport CreateUserComputation(IFormFile file)
    {
        var computation = new Computation(Config.WorkingDirectory);
        var savePath = Path.Combine(Config.WorkingDirectory, computation.ComputationId, file.FileName);

        using (var fileStream = new FileStream(savePath, FileMode.Create))
        {
            file.CopyTo(fileStream);
        }

        Init(computation);

        return computation.GetComputationReport();
    }

    public void Init(Computation c)
    {

        if (c.ComputationUnits.Last().Status != ComputationStatus.Initializing) return;

        var xml = BuildInitXML(c);
        RunMole(c, xml, true, false);
    }





    /// <summary>
    /// Given the user provided ComputationID returns Computation object. Null in case such computation does not exist.
    /// </summary>
    /// <param name="workingDirectory">Working directory of this API.</param>
    /// <param name="computationId">user provided computation id</param>
    /// <returns>Computation object representing a single user computation folder</returns>
    public Computation LoadComputation(string computationId)
    {
        var file = Path.Combine(Config.WorkingDirectory, computationId, MoleApiFiles.ComputationStatus);
        if (File.Exists(file))
        {
            return JsonConvert.DeserializeObject<Computation>(File.ReadAllText(file));
        }

        return null;
    }



    /// <summary>
    /// FOR USER INFORMATION ONLY!!
    /// Loads existing computation
    /// </summary>
    /// <param name="computationId">Id of computation</param>
    /// <param name="submitId">Id of submission</param>
    /// <returns>Computation report</returns>
    internal ComputationReport GetComputationReport(string computationId, int submitId = -1)
    {
        var cpt = LoadComputation(computationId);

        if (cpt == null)
        {
            return Computation.NotExists(computationId);
        }
        else return cpt.GetComputationReport(submitId);
    }




    internal string CanRun(Computation c)
    {
        var cpt = c.GetComputationReport();

        if (string.Equals(cpt.Status, ComputationStatus.Running))
            return "Previous computation is still running. This one has not been started. Please, try again later.";

        if (string.Equals(cpt.Status, ComputationStatus.Initializing))
            return "The structure is still being initialized. This computation has not been started. Please, try again later.";


        if (string.Equals(cpt.Status, ComputationStatus.Deleted) | string.Equals(cpt.Status, ComputationStatus.FailedInitialization))
            return $"{cpt.Status}... cannot proceed.";


        if (RunningProcesses.Count >= Config.MaxConcurentComputations)
            return "Server is under heavy load, computation has not started. Please try again later";


        return string.Empty;
    }



    internal ComputationReport InitAndRunEBI(string pdbId, ComputationParameters pars)
    {
        var computation = new Computation(Config.WorkingDirectory, pdbId);
        computation.DownloadStructure();
        if (computation.ComputationUnits.Last().Status == ComputationStatus.FailedInitialization) return computation.GetComputationReport();

        Directory.CreateDirectory(computation.SubmitDirectory(1));

        return PrepareAndRunMole(computation, pars);
    }


    /// <summary>
    /// Returns information about all computations carried out so far
    /// </summary>
    /// <param name="computationId"></param>
    /// <returns></returns>
    internal string ComputationInfos(string computationId)
    {
        var cpt = LoadComputation(computationId);
        if (cpt == null) return Computation.NotExists(computationId).ToJson();

        var directories = Directory.GetDirectories(Path.Combine(Config.WorkingDirectory, computationId)).Where(x => Path.GetFileNameWithoutExtension(x) != "0").ToArray();

        return JsonConvert.SerializeObject(new
        {
            ComputationId = computationId,
            cpt.UserStructure,
            cpt.PdbId,
            cpt.AssemblyId,
            Submissions = directories.Select((x, i) => new
            {
                SubmitId = Path.GetFileName(x),
                cpt.ComputationUnits[i].Status,
                MoleConfig = File.Exists(Path.Combine(x, MoleApiFiles.MoleParams)) ? JsonConvert.DeserializeObject(File.ReadAllText(Path.Combine(x, MoleApiFiles.MoleParams))) : new object(),
                PoresConfig = File.Exists(Path.Combine(x, MoleApiFiles.PoreParams)) ? JsonConvert.DeserializeObject<PoresParameters>(File.ReadAllText(Path.Combine(x, MoleApiFiles.PoreParams))).UserParameters() : new object()
            })
        }, Formatting.Indented);
    }




    internal ComputationReport PrepareAndRunPores(Computation computation, APIPoresParameters p)
    {
        try
        {
            computation.ChangeStatus(ComputationStatus.Running);

            var input = Path.Combine(computation.SubmitDirectory(-1), MoleApiFiles.PoreParams);

            var json = new PoresParameters
            {
                WorkingDirectory = Path.Combine(computation.SubmitDirectory()),
                InMembrane = p.InMembrane,
                Chains = string.IsNullOrEmpty(p.Chains) ? [] : p.Chains.Split([',']),
                InteriorThreshold = p.InteriorThreshold == 0 ? 0.8 : p.InteriorThreshold,
                ProbeRadius = p.ProbeRadius == 0 ? 13.0 : p.ProbeRadius,
                PyMolLocation = Config.PyMOL,
                MemEmbedLocation = Config.MEMBED,
                IsBetaBarel = p.IsBetaBarel
            };

            if (computation.DbModePores) json.PdbId = computation.PdbId;
            else json.UserStructure = StructurePath(computation.ComputationId);

            File.WriteAllText(input, JsonConvert.SerializeObject(json, Formatting.Indented));

            RunPores(computation, input);

            return computation.GetComputationReport();
        }
        catch (Exception e)
        {
            computation.ChangeStatus(ComputationStatus.Error, e.Message);
            return computation.GetComputationReport();
        }
    }



    private void RunPores(Computation computation, string inputJson)
    {
        ProcessStartInfo info = new()
        {
            FileName = Config.Pores,
            Arguments = inputJson,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        RunComputation(computation, info, false);
    }



    internal ComputationReport PrepareAndRunMole(Computation c, ComputationParameters param)
    {
        try
        {
            c.ChangeStatus(ComputationStatus.Running);

            var xmlPath = BuildXML(c, param);
            File.WriteAllText(Path.Combine(c.SubmitDirectory(c.ComputationUnits.Count), MoleApiFiles.MoleParams), JsonConvert.SerializeObject(param, Formatting.Indented));

            RunMole(c, xmlPath, false);

            return c.GetComputationReport();
        }
        catch (Exception e)
        {
            c.ChangeStatus(ComputationStatus.Error, e.Message);
            return c.GetComputationReport();
        }
    }



    private void RunMole(Computation c, string xmlPath, bool init, bool zipDirectory = true)
    {
        ProcessStartInfo info = new()
        {
            FileName = Config.MOLE,
            Arguments = xmlPath,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        RunComputation(c, info, init, zipDirectory);

    }



    private void RunComputation(Computation c, ProcessStartInfo info, bool init, bool zipDirectory = true)
    {
        Task.Run(() => RunComputationAsync(c, info, init, zipDirectory));
    }


    /// <summary>
    /// Waits for a free slot (at most MaxConcurentComputations processes run at once, initializations included),
    /// runs the process and sets the computation status when it exits: Error when the process writes to stderr,
    /// exits with a non-zero code or the API itself fails (e.g. the process cannot be started), otherwise Initialized/Finished.
    /// A process killed by the user keeps the Aborted status set by KillComputation.
    /// </summary>
    private async Task RunComputationAsync(Computation c, ProcessStartInfo info, bool init, bool zipDirectory)
    {
        var job = new RunningJob();
        RunningProcesses[c.ComputationId] = job; // registered while waiting, so that KillComputation can abort it
        try
        {
            await RunJobAsync(c, info, init, zipDirectory, job);
        }
        finally
        {
            RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(c.ComputationId, job)); // only this run, not a later submission
        }
    }


    private async Task RunJobAsync(Computation c, ProcessStartInfo info, bool init, bool zipDirectory, RunningJob job)
    {
        await slots.WaitAsync();

        try
        {
            if (LoadComputation(c.ComputationId)?.ComputationUnits.Last().Status == ComputationStatus.Deleted) return; // deleted while waiting

            StringBuilder sb = new();

            using Process p = new() { StartInfo = info };
            p.ErrorDataReceived += (s, e) =>
            {
                if (e.Data != null) lock (sb) sb.AppendLine(e.Data);
            };
            p.OutputDataReceived += (s, e) => { }; // progress output is not needed, read it so the process never blocks on a full pipe

            lock (job)
            {
                if (job.Killed) return; // aborted while waiting, status set by KillComputation
                job.Process = p;
                p.Start();
            }
            p.BeginErrorReadLine();
            p.BeginOutputReadLine();

            try
            {
                await p.WaitForExitAsync(); // also waits until stderr is read completely
            }
            finally
            {
                RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(c.ComputationId, job)); // as soon as the process exits
            }

            if (job.Killed) return; // killed by the user, status set by KillComputation
            if (sb.Length != 0)
            {
                c.ChangeStatus(ComputationStatus.Error, sb.ToString());
                return;
            }
            if (p.ExitCode != 0)
            {
                c.ChangeStatus(ComputationStatus.Error, p.ExitCode == 137
                    ? "The computation was terminated by the system, most likely it ran out of memory."
                    : $"The computation failed with exit code {p.ExitCode}.");
                return;
            }
            if (zipDirectory) Utils.Extensions.ZipDirectories(Path.Combine(Config.WorkingDirectory, c.ComputationId, c.GetComputationReport().SubmitId.ToString()), StructurePath(c.ComputationId));

            if (init)
            {
                c.ChangeStatus(ComputationStatus.Initialized);
            }
            else c.ChangeStatus(ComputationStatus.Finished);
        }
        catch (Exception e)
        {
            try { c.ChangeStatus(ComputationStatus.Error, e.Message); } catch (Exception) { } // never leave the computation running
        }
        finally
        {
            slots.Release();
        }
    }



    internal ComputationReport DeleteComputation(string id)
    {
        var cpt = LoadComputation(id);
        if (cpt.ComputationId != null)
        {
            KillComputation(cpt);

            cpt.ChangeStatus(ComputationStatus.Deleted);

            var report = cpt.GetComputationReport();
            report.Status = ComputationStatus.Deleted;
            return report;
        }
        return Computation.NotExists(id);

    }



    internal ComputationReport KillComputation(Computation cpt)
    {

        if (RunningProcesses.TryGetValue(cpt.ComputationId, out var job))
        {
            try
            {
                lock (job)
                {
                    job.Killed = true;
                    job.Process?.Kill(entireProcessTree: true); // Pores starts PyMOL and MEMEMBED; no process yet: still waiting, it will not start
                }
                RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(cpt.ComputationId, job));

                cpt.ChangeStatus(ComputationStatus.Aborted);
            }
            catch (Exception)
            {
                job.Killed = false;
                return new ComputationReport()
                {
                    ComputationId = cpt.ComputationId,
                    SubmitId = cpt.ComputationUnits.Count,
                    Status = ComputationStatus.Finished,
                    ErrorMsg = "Something went wrong during the process termination."
                };
            }
            return cpt.GetComputationReport();
        }
        else
        {
            var report = cpt.GetComputationReport();
            report.ErrorMsg = $"Cannot kill computation with the stuatus \"{report.Status}\"";

            return report;

        }
    }



    /// <summary>
    /// Downloads file requested for user download
    /// </summary>
    /// <param name="computationId">ComputaionId</param>
    /// <param name="submitId">Submit</param>
    /// <param name="type">Type of data to be downloaded: pymol/chimera/vmd/pdb/json/report default:json</param>
    /// <returns></returns>
    internal (byte[], string) QueryFile(string computationId, int submitId, string type)
    {
        byte[] bytes;
        switch (type)
        {

            case "json":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "json", MoleApiFiles.DataJson));
                return (bytes, $"mole_channels_{computationId}_{submitId}.json");
            case "report":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), MoleApiFiles.Report));
                return (bytes, $"mole_channels_{computationId}_{submitId}.zip");
            case "molecule":
                var molecule = StructurePath(computationId);
                return (File.ReadAllBytes(molecule), Path.GetFileName(molecule));
            case "pymol":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "pymol", MoleApiFiles.PythonScript));
                return (bytes, $"mole_channels_{computationId}_{submitId}_pymol.py");
            case "chimera":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "chimera", MoleApiFiles.PythonScript));
                return (bytes, $"mole_channels_{computationId}_{submitId}_chimera.py");
            case "vmd":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "vmd", MoleApiFiles.VMDScript));
                return (bytes, $"mole_channels_{computationId}_{submitId}.tk");
            case "pdb":
                var zipDir = Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "pdb", "profile");
                bytes = Utils.Extensions.ZipDirectory(zipDir);
                return (bytes, $"mole_channels_{computationId}_{submitId}_pdb.zip");
            case "membrane":
                bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, "membrane.json"));
                return (bytes, "membrane.json");

            default:
                return (File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "json", MoleApiFiles.DataJson)), $"mole_channels_{computationId}_{submitId}.json");
        }


    }

    /// <summary>
    /// Retrieves CSA anotated active sites for a given pdb id
    /// </summary>
    /// <param name="pdbId">pdbId to be processed</param>
    /// <returns></returns>
    internal CSAResidue[][] GetActiveSite(string pdbId)
    {
        if (csa.Database.ContainsKey(pdbId)) return [.. csa.Database[pdbId].Select(x => x.Residues.ToArray())];

        return [];
    }
    #endregion



    #region Helpers
    /// <summary>
    /// Path to the structure file of the computation: the first non-.json file in its folder
    /// </summary>
    /// <param name="computationId">Id of computation</param>
    /// <returns>Complete path to the structure file</returns>
    private string StructurePath(string computationId) =>
        Directory.GetFiles(Path.Combine(Config.WorkingDirectory, computationId)).First(x => Path.GetExtension(x) != ".json");


    /// <summary>
    /// Builds input XML for complex MOLE computation
    /// </summary>
    /// <param name="c">Parrent computation </param>
    /// <param name="param">Parameters provided by the user for the specified job</param>
    /// <returns></returns>
    private string BuildXML(Computation c, ComputationParameters param)
    {
        var hasOrigin = !param.Origin?.IsEmpty() ?? false;
        var hasExits = !param.CustomExits?.IsEmpty() ?? false;

        var structure = StructurePath(c.ComputationId);
        var structureName = Path.GetFileNameWithoutExtension(structure);

        var root = new XElement("Tunnels");
        var input = new XElement("Input",
            new XAttribute("SpecificChains", param.Input.SpecificChains),
            new XAttribute("ReadAllModels", param.Input.ReadAllModels ? "1" : "0"), structure);
        var WD = new XElement("WorkingDirectory", Path.Combine(Path.GetDirectoryName(structure), c.GetComputationReport().SubmitId.ToString()));

        var nonActive = new XElement("NonActiveParts");

        if (!param.NonActiveResidues.IsNullOrEmpty())
        {
            nonActive.Add(BuildResiduesElement(param.NonActiveResidues));
        }

        if (!param.QueryFilter.IsNullOrEmpty())
        {
            nonActive.Add(new XElement("Query", param.QueryFilter));
        }

        var par = new XElement("Params",
            new XElement("Cavity",
                new XAttribute("ProbeRadius", param.Cavity.ProbeRadius),
                new XAttribute("InteriorThreshold", param.Cavity.InteriorThreshold),
                new XAttribute("IgnoreHETAtoms", param.Cavity.IgnoreHETAtoms ? "1" : "0"),
                new XAttribute("IgnoreHydrogens", param.Cavity.IgnoreHydrogens ? "1" : "0")),
            new XElement("Tunnel",
                new XAttribute("BottleneckRadius", param.Tunnel.BottleneckRadius),
                new XAttribute("BottleneckTolerance", param.Tunnel.BottleneckTolerance),
                new XAttribute("MaxTunnelSimilarity", param.Tunnel.MaxTunnelSimilarity),
                new XAttribute("OriginRadius", param.Tunnel.OriginRadius),
                new XAttribute("SurfaceCoverRadius", param.Tunnel.SurfaceCoverRadius),
                new XAttribute("WeightFunction", param.Tunnel.WeightFunction),
                new XAttribute("UseCustomExitsOnly", hasOrigin & hasExits ? "1" : "0")));

        var export = new XElement("Export",
            new XElement("Formats",
                new XAttribute("ChargeSurface", "0"),
                new XAttribute("PyMol", "1"),
                new XAttribute("PDBProfile", "1"),
                new XAttribute("VMD", "1"),
                new XAttribute("Chimera", "1"),
                new XAttribute("CSV", "1"),
                new XAttribute("JSON", "1")),
             new XElement("Types",
                new XAttribute("Cavities", "0"),
                new XAttribute("Tunnels", (!hasOrigin && hasExits) ? "0" : "1"),
                new XAttribute("PoresAuto", param.PoresAuto ? "1" : "0"),
                new XAttribute("PoresMerged", param.PoresMerged ? "1" : "0"),
                new XAttribute("PoresUser", (!hasOrigin & hasExits) ? "1" : "0")),
             new XElement("PyMol",
                new XAttribute("PDBId", structureName),
                new XAttribute("SurfaceType", "Spheres")),
             new XElement("VMD",
                new XAttribute("PDBId", structureName),
                new XAttribute("SurfaceType", "Spheres")),
             new XElement("Chimera",
                new XAttribute("PDBId", structureName),
                new XAttribute("SurfaceType", "Spheres")));


        var origins = BuildOriginElement(param.Origin, "Origin", "Origin");

        root.Add(input);
        root.Add(WD);
        root.Add(nonActive);
        root.Add(par);
        root.Add(export);
        root.Add(origins);

        if (hasExits)
        {
            var customExits = BuildOriginElement(param.CustomExits, "CustomExit", "Exit");
            root.Add(customExits);
        }

        var path = Path.Combine(c.SubmitDirectory(c.GetComputationReport().SubmitId), MoleApiFiles.InputXML);
        root.Save(path);
        return path;
    }


    private string BuildInitXML(Computation c)
    {
        var param = new ComputationParameters();

        var structure = StructurePath(c.ComputationId);

        var root = new XElement("Tunnels");
        var input = new XElement("Input", structure);
        var WD = new XElement("WorkingDirectory", Path.Combine(Path.GetDirectoryName(structure), "0"));

        var par = new XElement("Params",
            new XElement("Cavity",
                new XAttribute("ProbeRadius", param.Cavity.ProbeRadius),
                new XAttribute("InteriorThreshold", param.Cavity.InteriorThreshold),
                new XAttribute("IgnoreHETAtoms", param.Cavity.IgnoreHETAtoms ? "1" : "0"),
                new XAttribute("IgnoreHydrogens", param.Cavity.IgnoreHydrogens ? "1" : "0")));

        var export = new XElement("Export",
            new XElement("Formats",
                new XAttribute("ChargeSurface", "0"),
                new XAttribute("PyMol", "0"),
                new XAttribute("PDBProfile", "0"),
                new XAttribute("VMD", "0"),
                new XAttribute("Chimera", "0"),
                new XAttribute("CSV", "0"),
                new XAttribute("JSON", "1")),
             new XElement("Types",
                new XAttribute("Cavities", "0"),
                new XAttribute("Tunnels", "0"),
                new XAttribute("PoresAuto", param.PoresAuto ? "1" : "0"),
                new XAttribute("PoresMerged", param.PoresMerged ? "1" : "0"),
                new XAttribute("PoresUser", (param.CustomExits?.IsEmpty() ?? true) ? "0" : "1")));


        var origins = new XElement("Origins", new XAttribute("Auto", "0"));

        root.Add(input);
        root.Add(WD);
        root.Add(par);
        root.Add(export);
        root.Add(origins);

        var submitDir = c.SubmitDirectory(0);
        Directory.CreateDirectory(submitDir);
        var path = Path.Combine(submitDir, MoleApiFiles.InputXML);
        root.Save(path);
        return path;

    }

    /// <summary>
    /// Builds Origin element. If null or empty automatic start points are used for calculation.
    /// PatternQuery expression, Residues[] and Points3D[] are all included
    /// </summary>
    /// <param name="o">Origin parameter</param>
    /// <returns>XElement for the MOLE input XML</returns>
    private XElement BuildOriginElement(Origin o, string key, string subKey)
    {
        if (o == null) return new XElement("Origins", new XAttribute("Auto", "1"));

        var element = new XElement($"{key}s");
        if (o?.IsEmpty() ?? true)
            return new XElement($"{key}s", new XAttribute("Auto", "1"));

        if (!o.Points.IsNullOrEmpty()) element.Add(o.Points.Select(x => new XElement($"{subKey}", new XElement("Point", new XAttribute("X", x.X), new XAttribute("Y", x.Y), new XAttribute("Z", x.Z)))));

        if (!o.Residues.IsNullOrEmpty())
        {
            foreach (var item in o.Residues)
            {
                element.Add(new XElement($"{subKey}", BuildResiduesElement(item)));
            }
        }

        if (!o.QueryExpression.IsNullOrEmpty()) element.Add(new XElement($"{subKey}", new XElement("Query", o.QueryExpression)));

        return element;
    }


    /// <summary>
    /// Given the list of residues builds their XML notation
    /// </summary>
    /// <param name="residues"></param>
    /// <returns></returns>
    private XElement[] BuildResiduesElement(Residue[] residues) =>
        [.. residues.Select(x => new XElement("Residue", new XAttribute("SequenceNumber", x.SequenceNumber), new XAttribute("Chain", x.Chain)))];
    #endregion

}
