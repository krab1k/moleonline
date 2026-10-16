using Mole.API.Utils;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
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
    /// Held by a submission from loading the computation until it is Running, so that parallel requests cannot all see it idle
    /// </summary>
    public Lock SubmitLock { get; } = new();

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

        computation.DownloadStructureAsync().ContinueWith(x => Init(computation)); // no thread is held while waiting for PDBe

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
        computation.DownloadBioStructureAsync().ContinueWith(x => Init(computation));


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
        var savePath = Path.Combine(Config.WorkingDirectory, computation.ComputationId, Path.GetFileName(file.FileName)); // the name comes from the client, never let it leave the folder

        using (var fileStream = new FileStream(savePath, FileMode.Create))
        {
            file.CopyTo(fileStream);
        }
        computation.StructureFile = Path.GetFileName(savePath);
        computation.SaveStatus();

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
            var computation = JsonConvert.DeserializeObject<Computation>(File.ReadAllText(file));
            var status = computation.ComputationUnits.Last().Status;
            if ((status == ComputationStatus.Running || status == ComputationStatus.Initializing) && computation.ApiInstance != Computation.CurrentApiInstance)
                computation.ChangeStatus(status == ComputationStatus.Running ? ComputationStatus.Error : ComputationStatus.FailedInitialization,
                    "The computation was interrupted by a restart of the server. Please, run it again.");
            return computation;
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




    /// <returns>Why a new submission cannot start, with the HTTP status; null when it can</returns>
    internal (int StatusCode, string Message)? CanRun(Computation c)
    {
        var cpt = c.GetComputationReport();

        if (string.Equals(cpt.Status, ComputationStatus.Running))
            return (StatusCodes.Status409Conflict, "Previous computation is still running. This one has not been started. Please, try again later.");

        if (string.Equals(cpt.Status, ComputationStatus.Initializing))
            return (StatusCodes.Status409Conflict, "The structure is still being initialized. This computation has not been started. Please, try again later.");

        if (string.Equals(cpt.Status, ComputationStatus.Deleted))
            return (StatusCodes.Status410Gone, "The computation has been deleted.");

        if (string.Equals(cpt.Status, ComputationStatus.FailedInitialization))
            return (StatusCodes.Status409Conflict, "The structure could not be loaded, no computation can run on it.");

        if (RunningProcesses.Count >= Config.MaxConcurentComputations)
            return (StatusCodes.Status503ServiceUnavailable, "Server is under heavy load, computation has not started. Please try again later");

        return null;
    }



    /// <summary>
    /// Returns information about all computations carried out so far
    /// </summary>
    /// <param name="computationId"></param>
    /// <returns></returns>
    internal string ComputationInfos(Computation cpt)
    {
        var computationId = cpt.ComputationId;

        // submissions with a folder, each with its own status (directories are not listed in any particular order)
        var submissions = cpt.ComputationUnits
            .Select(u => (unit: u, dir: Path.Combine(Config.WorkingDirectory, computationId, u.SubmitId.ToString())))
            .Where(x => Directory.Exists(x.dir));

        return JsonConvert.SerializeObject(new
        {
            ComputationId = computationId,
            cpt.UserStructure,
            cpt.PdbId,
            cpt.AssemblyId,
            Submissions = submissions.Select(s => (s.unit, mole: Path.Combine(s.dir, MoleApiFiles.MoleParams), pores: Path.Combine(s.dir, MoleApiFiles.PoreParams))).Select(s => new
            {
                s.unit.SubmitId,
                Mode = File.Exists(s.mole) ? "Mole" : File.Exists(s.pores) ? "Pores" : null, // as in Submit/Mole and Submit/Pores
                s.unit.Status,
                s.unit.ErrorMsg,
                MoleConfig = File.Exists(s.mole) ? JsonConvert.DeserializeObject(File.ReadAllText(s.mole)) : new object(),
                PoresConfig = File.Exists(s.pores) ? JsonConvert.DeserializeObject<PoresParameters>(File.ReadAllText(s.pores)).UserParameters() : new object()
            })
        }, Formatting.Indented);
    }




    /// <returns>The report when started (taken before the run can change it), or the error; Started tells which</returns>
    internal (ComputationReport Report, bool Started) PrepareAndRunPores(Computation computation, APIPoresParameters p)
    {
        try
        {
            computation.ChangeStatus(ComputationStatus.Running);

            var input = Path.Combine(computation.SubmitDirectory(-1), MoleApiFiles.PoreParams);

            var json = new PoresParameters
            {
                Structure = computation.StructurePath(),
                PdbId = computation.DbModePores ? computation.PdbId : null,
                WorkingDirectory = computation.SubmitDirectory(),
                Membrane = Path.Combine(Config.WorkingDirectory, computation.ComputationId, MoleApiFiles.Membrane),
                InMembrane = p.InMembrane,
                IsBetaBarel = p.IsBetaBarel,
                Chains = p.Chains?.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries) ?? [],
                ProbeRadius = p.ProbeRadius is null or 0 ? 13.0 : p.ProbeRadius.Value,
                InteriorThreshold = p.InteriorThreshold is null or 0 ? 0.8 : p.InteriorThreshold.Value,
            };

            File.WriteAllText(input, JsonConvert.SerializeObject(json, Formatting.Indented));

            var started = computation.GetComputationReport();
            RunPores(computation, input);
            return (started, true);
        }
        catch (Exception e)
        {
            computation.ChangeStatus(ComputationStatus.Error, e.Message);
            return (computation.GetComputationReport(), false);
        }
    }



    /// <summary>
    /// Pores computes the membrane and writes the MOLE input of a path across it (input.xml), MOLE then computes the pore.
    /// </summary>
    private void RunPores(Computation computation, string inputJson)
    {
        var submitDirectory = Path.GetDirectoryName(inputJson);
        ProcessStartInfo info = new()
        {
            FileName = Config.Pores,
            ArgumentList = { inputJson }, // one argument even with spaces in the path
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        RunComputation(computation, [info, MoleProcess(Path.Combine(submitDirectory, MoleApiFiles.InputXML))], false, check: () =>
        {
            var data = Path.Combine(submitDirectory, "json", MoleApiFiles.DataJson);
            if (!File.Exists(data)) return "MOLE did not produce any result.";
            var paths = JObject.Parse(File.ReadAllText(data))["Channels"]?["Paths"] as JArray;
            return paths == null || paths.Count == 0 ? "No pore was found along the membrane normal." : null;
        });
    }



    /// <returns>The report when started (taken before the run can change it), or the error; Started tells which</returns>
    internal (ComputationReport Report, bool Started) PrepareAndRunMole(Computation c, ComputationParameters param)
    {
        try
        {
            c.ChangeStatus(ComputationStatus.Running);

            // only the first model is used (the web shows only that one); MOLE parses mmCIF as PDB with this option.
            // Still accepted and stored (as false), the web requires it in imported parameters.
            param.Input.ReadAllModels = false;
            var xmlPath = BuildXML(c, param);
            File.WriteAllText(Path.Combine(c.SubmitDirectory(c.ComputationUnits.Count), MoleApiFiles.MoleParams), JsonConvert.SerializeObject(param, Formatting.Indented));

            var started = c.GetComputationReport();
            RunMole(c, xmlPath, false);
            return (started, true);
        }
        catch (Exception e)
        {
            c.ChangeStatus(ComputationStatus.Error, e.Message);
            return (c.GetComputationReport(), false);
        }
    }



    private void RunMole(Computation c, string xmlPath, bool init, bool zipDirectory = true)
    {
        RunComputation(c, [MoleProcess(xmlPath)], init, zipDirectory);
    }


    private ProcessStartInfo MoleProcess(string xmlPath) => new()
    {
        FileName = Config.MOLE,
        ArgumentList = { xmlPath }, // one argument even with spaces in the path
        RedirectStandardOutput = true,
        RedirectStandardError = true,
    };



    private void RunComputation(Computation c, ProcessStartInfo[] steps, bool init, bool zipDirectory = true, Func<string> check = null)
    {
        Task.Run(() => RunComputationAsync(c, steps, init, zipDirectory, check));
    }


    /// <summary>
    /// Waits for a free slot (at most MaxConcurentComputations computations run at once, initializations included),
    /// runs the processes one after another and sets the computation status: Error when a process writes to stderr,
    /// exits with a non-zero code, the check after the last one fails or the API itself fails (e.g. a process cannot be started),
    /// otherwise Initialized/Finished. A computation killed by the user keeps the Aborted status set by KillComputation.
    /// </summary>
    private async Task RunComputationAsync(Computation c, ProcessStartInfo[] steps, bool init, bool zipDirectory, Func<string> check)
    {
        var job = new RunningJob();
        RunningProcesses[c.ComputationId] = job; // registered while waiting, so that KillComputation can abort it
        try
        {
            await RunJobAsync(c, steps, init, zipDirectory, check, job);
        }
        finally
        {
            RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(c.ComputationId, job)); // only this run, not a later submission
        }
    }


    private async Task RunJobAsync(Computation c, ProcessStartInfo[] steps, bool init, bool zipDirectory, Func<string> check, RunningJob job)
    {
        await slots.WaitAsync();

        try
        {
            if (LoadComputation(c.ComputationId)?.ComputationUnits.Last().Status == ComputationStatus.Deleted) return; // deleted while waiting

            using var timeLimit = new CancellationTokenSource(TimeSpan.FromMinutes(Config.MaxComputationMinutes)); // from now, the queue does not count
            foreach (var info in steps)
            {
                var (killed, error) = await RunProcessAsync(info, job, timeLimit.Token);
                if (killed) return; // killed by the user, status set by KillComputation
                if (timeLimit.IsCancellationRequested)
                {
                    c.ChangeStatus(ComputationStatus.Error, $"The computation exceeded the time limit of {Config.MaxComputationMinutes} minute{(Config.MaxComputationMinutes == 1 ? "" : "s")}.");
                    return;
                }
                if (error != null)
                {
                    c.ChangeStatus(ComputationStatus.Error, error);
                    return;
                }
            }
            RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(c.ComputationId, job)); // as soon as the last process exits

            var checkError = check?.Invoke();
            if (checkError != null)
            {
                c.ChangeStatus(ComputationStatus.Error, checkError);
                return;
            }
            if (zipDirectory) Utils.Extensions.ZipDirectories(Path.Combine(Config.WorkingDirectory, c.ComputationId, c.GetComputationReport().SubmitId.ToString()), c.StructurePath());

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


    /// <summary>
    /// Runs one process of a computation. Returns whether the user killed it, or the error when it failed.
    /// When the time limit expires, the process is killed (the caller reports it).
    /// </summary>
    private static async Task<(bool killed, string error)> RunProcessAsync(ProcessStartInfo info, RunningJob job, CancellationToken timeLimit)
    {
        StringBuilder sb = new();

        using Process p = new() { StartInfo = info };
        p.ErrorDataReceived += (s, e) =>
        {
            if (e.Data != null) lock (sb) sb.AppendLine(e.Data);
        };
        p.OutputDataReceived += (s, e) => { }; // progress output is not needed, read it so the process never blocks on a full pipe

        lock (job)
        {
            if (job.Killed) return (true, null); // aborted while waiting or between processes, status set by KillComputation
            if (timeLimit.IsCancellationRequested) return (false, null);
            job.Process = p;
            p.Start();
        }
        p.BeginErrorReadLine();
        p.BeginOutputReadLine();

        try
        {
            await p.WaitForExitAsync(timeLimit); // also waits until stderr is read completely
        }
        catch (OperationCanceledException)
        {
            p.Kill(entireProcessTree: true);
            // the output closes when the process and its descendants are gone; one that left the tree (its parent exited)
            // may keep it open, do not let it hold the slot
            using var grace = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            try { await p.WaitForExitAsync(grace.Token); } catch (OperationCanceledException) { }
            return (job.Killed, null); // a kill by the user meanwhile wins, it set the status
        }
        finally
        {
            lock (job) job.Process = null; // exited and disposed below, a kill now stops the next process
        }

        if (job.Killed) return (true, null);
        if (sb.Length != 0) return (false, sb.ToString());
        if (p.ExitCode != 0)
            return (false, p.ExitCode == 137
                ? "The computation was terminated by the system, most likely it ran out of memory."
                : $"The computation failed with exit code {p.ExitCode}.");
        return (false, null);
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



    /// <returns>The report, with the HTTP status of the request</returns>
    internal (ComputationReport Report, int StatusCode) KillComputation(Computation cpt)
    {

        if (RunningProcesses.TryGetValue(cpt.ComputationId, out var job))
        {
            try
            {
                lock (job)
                {
                    job.Killed = true;
                    job.Process?.Kill(entireProcessTree: true); // Pores may start MEMEMBED; no process: waiting or between processes, the next one will not start
                }
                RunningProcesses.TryRemove(new KeyValuePair<string, RunningJob>(cpt.ComputationId, job));

                cpt.ChangeStatus(ComputationStatus.Aborted);
            }
            catch (Exception)
            {
                job.Killed = false;
                var failed = cpt.GetComputationReport(); // still running
                failed.Status = ComputationStatus.Error;
                failed.ErrorMsg = "The computation could not be stopped. Please, try again.";
                return (failed, StatusCodes.Status500InternalServerError);
            }
            return (cpt.GetComputationReport(), StatusCodes.Status200OK);
        }
        else
        {
            var report = cpt.GetComputationReport();
            report.ErrorMsg = $"Cannot kill computation with the status \"{report.Status}\"";
            report.Status = ComputationStatus.Error;

            return (report, StatusCodes.Status409Conflict);

        }
    }



    /// <summary>
    /// Downloads file requested for user download
    /// </summary>
    /// <param name="computationId">ComputaionId</param>
    /// <param name="submitId">Submit</param>
    /// <param name="type">Type of data to be downloaded: pymol/chimera/vmd/pdb/json/report default:json</param>
    /// <returns></returns>
    internal static readonly string[] DataFormats = ["json", "report", "molecule", "pymol", "chimera", "vmd", "pdb", "membrane"];

    internal (Stream, string) QueryFile(string computationId, int submitId, string type)
    {
        Stream stream; // files are streamed, not read into memory
        switch (type)
        {

            case "json":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "json", MoleApiFiles.DataJson));
                return (stream, $"mole_channels_{computationId}_{submitId}.json");
            case "report":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), MoleApiFiles.Report));
                return (stream, $"mole_channels_{computationId}_{submitId}.zip");
            case "molecule":
                var molecule = LoadComputation(computationId).StructurePath();
                return (File.OpenRead(molecule), Path.GetFileName(molecule));
            case "pymol":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "pymol", MoleApiFiles.PythonScript));
                return (stream, $"mole_channels_{computationId}_{submitId}_pymol.py");
            case "chimera":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "chimera", MoleApiFiles.PythonScript));
                return (stream, $"mole_channels_{computationId}_{submitId}_chimera.py");
            case "vmd":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "vmd", MoleApiFiles.VMDScript));
                return (stream, $"mole_channels_{computationId}_{submitId}.tk");
            case "pdb":
                var zipDir = Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "pdb", "profile");
                stream = new MemoryStream(Utils.Extensions.ZipDirectory(zipDir));
                return (stream, $"mole_channels_{computationId}_{submitId}_pdb.zip");
            case "membrane":
                stream = File.OpenRead(Path.Combine(Config.WorkingDirectory, computationId, MoleApiFiles.Membrane));
                return (stream, MoleApiFiles.Membrane);

            default:
                throw new ArgumentOutOfRangeException(nameof(type), type, "Unknown data format.");
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
    /// Builds input XML for complex MOLE computation
    /// </summary>
    /// <param name="c">Parrent computation </param>
    /// <param name="param">Parameters provided by the user for the specified job</param>
    /// <returns></returns>
    private string BuildXML(Computation c, ComputationParameters param)
    {
        var hasOrigin = !param.Origin?.IsEmpty() ?? false;
        var hasExits = !param.CustomExits?.IsEmpty() ?? false;

        var structure = c.StructurePath();
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

        var structure = c.StructurePath();

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
