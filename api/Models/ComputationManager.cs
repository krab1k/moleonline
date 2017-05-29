using Mole.API.Utils;
using Newtonsoft.Json;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Xml.Linq;
using System.Text;
using System.Collections.Generic;
using Microsoft.AspNetCore.Http;
using System.Threading;
using System;
using System.ComponentModel;
using System.Net;

namespace Mole.API.Models
{
    public class ComputationManager
    {
        private Config Config;
        private object locker = new object();
        private ObservableDictionary<string, int> RunningProcesses;

        public ComputationManager(Config c)
        {
            var f = Path.Combine(c.WorkingDirectory, MoleApiFiles.RunningProcesses);

            this.Config = c;
            if (File.Exists(f)) RunningProcesses = JsonConvert.DeserializeObject<ObservableDictionary<string, int>>(File.ReadAllText(f));
            else RunningProcesses = new ObservableDictionary<string, int>();

            RunningProcesses.CollectionChanged += (s, e) =>
            {
                File.WriteAllText(f, JsonConvert.SerializeObject(RunningProcesses, Formatting.Indented));
            };
        }




        #region Methods
        /// <summary>
        /// Creates a MOLE computation with a given PDB id
        /// </summary>
        /// <param name="id">PDB id</param>
        /// <param name="bioUnit">optionall biologicall assembly</param>
        /// <returns>(true, computationalID) - in case the file is downloadable (false, ErrorMessage) - otherwise</returns>
        public ComputationReport CreatePDBComputation(string id, string assemblyId)
        {
            var computation = new Computation(Config.WorkingDirectory, id, assemblyId);

            Task.Run(() => computation.DownloadStructure());

            return computation.GetComputationReport(1);
        }



        internal ComputationReport CreatePoreComputation(string pdbId)
        {
            using (WebClient cl = new WebClient())
            {
                Computation cpt = null;
                try
                {
                    var xml = cl.DownloadString($@"http://www.ebi.ac.uk/pdbe/static/entry/download/{pdbId}-assembly.xml");
                    var id = XDocument.Parse(xml).Root.Elements("assembly").First(x => x.Attribute("prefered").Value.Equals("True")).Attribute("id").Value;

                    cpt = new Computation(Config.WorkingDirectory, pdbId, id);
                    cpt.DbModePores = true;
                    cpt.DownloadStructure();
                }
                catch (Exception)
                {
                    ComputationReport report = null;

                    if (cpt == null)
                    {
                        report = new ComputationReport()
                        {
                            ComputationId = null,
                            SubmitId = 0,
                            Status = ComputationStatus.FailedInitialization,
                            ErrorMsg = $"Structure [{pdbId}] is unlikely to exist or has been made obsolete."
                        };
                    }
                    else
                    {
                        cpt.ChangeStatus(ComputationStatus.FailedInitialization, $"Structure [{pdbId}] is unlikely to exist or has been made obsolete.");
                    }
                }
                return cpt.GetComputationReport();
            }
        }



        /// <summary>
        /// Handles user computations with user file provided
        /// </summary>
        /// <param name="file"></param>
        /// <returns></returns>
        public ComputationReport CreateUserComputation(IFormFile file)
        {
            var cpt = new Computation(Config.WorkingDirectory);
            var savePath = Path.Combine(Config.WorkingDirectory, cpt.ComputationId, file.FileName);

            using (var fileStream = new FileStream(savePath, FileMode.Create))
            {
                file.CopyToAsync(fileStream);
            }
            return cpt.GetComputationReport();
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
            if (File.Exists(file)) return JsonConvert.DeserializeObject<Computation>(File.ReadAllText(file));

            return null;
        }



        /// <summary>
        /// FOR USER INFORMATION ONLY!!
        /// Loads existing computation
        /// </summary>
        /// <param name="computationId">Computation id</param>
        /// <returns>Computation object</returns>
        internal ComputationReport GetComputationReport(string computationId, int submitId = 0)
        {
            var cpt = LoadComputation(computationId);

            if (cpt == null)
            {
                return Computation.NotExists(computationId);
            }
            else return cpt.GetComputationReport(submitId);
        }



        internal (bool, string) CanRun(Computation c)
        {
            var cpt = c.GetComputationReport();

            if (string.Equals(cpt.Status, ComputationStatus.Running))
                return (false, "Previous computation is still running. This one has not been started. Please, try again later.");


            if (string.Equals(cpt.Status, ComputationStatus.Deleted) | string.Equals(cpt.Status, ComputationStatus.FailedInitialization))
                return (false, $"{cpt.Status}... cannot proceed.");


            if (RunningProcesses.Count >= Config.MaxConcurentComputations)
                return (false, "Server is under heavy load, computation has not started. Please try again later");


            return (true, string.Empty);
        }


        internal ComputationReport InitAndRunEBI(string pdbId, ComputationParameters pars)
        {
            var computation = new Computation(Config.WorkingDirectory, pdbId);
            computation.DownloadStructure();
            

            Task.Run(() => RunEBIRoutine(computation, pars));

            return computation.GetComputationReport();
        }



        private void RunEBIRoutine(Computation cpt, ComputationParameters pars)
        {
            var attempts = 0;

            while (LoadComputation(cpt.ComputationId).GetComputationReport().Status == ComputationStatus.Initializing)
            {
                attempts++;
                Thread.Sleep(1000);
            }

            if (attempts > 30)
            {
                LoadComputation(cpt.ComputationId).ChangeStatus(ComputationStatus.FailedInitialization);
                return;
            };

            cpt = LoadComputation(cpt.ComputationId);
            if (cpt.GetComputationReport().Status != ComputationStatus.Initialized) return;

            cpt.AddCalculation();
            PrepareAndRunMole(cpt, pars);
        }


        /// <summary>
        /// Returns information about all computations carried out so far
        /// </summary>
        /// <param name="computationId"></param>
        /// <returns></returns>
        internal dynamic ComputationInfos(string computationId)
        {
            var cpt = LoadComputation(computationId);
            if (cpt == null) return Computation.NotExists(computationId);

            var directories = Directory.GetDirectories(Path.Combine(Config.WorkingDirectory, computationId));

            return new
            {
                ComputationId = computationId,
                UserStructure = cpt.UserStructure,
                PdbId = cpt.PdbId,
                AssemblyId = cpt.AssemblyId,
                Submissions = directories.Select(x => new
                {
                    SubmitId = Path.GetFileName(x),
                    MoleConfig = File.Exists(Path.Combine(x, MoleApiFiles.MoleParams)) ? JsonConvert.DeserializeObject(File.ReadAllText(Path.Combine(x, MoleApiFiles.MoleParams))) : new object(),
                    PoresConfig = File.Exists(Path.Combine(x, MoleApiFiles.PoreParams)) ? JsonConvert.DeserializeObject<PoresParameters>(File.ReadAllText(Path.Combine(x, MoleApiFiles.PoreParams))).UserParameters() : new object()
                })
            };
        }




        internal ComputationReport PrepareAndRunPores(Computation computation, bool isBetaStructure, bool inMembraneMode, string[] chains)
        {
            computation.ChangeStatus(ComputationStatus.Running);

            var input = Path.Combine(computation.SubmitDirectory(0), MoleApiFiles.PoreParams);

            var json = computation.DbModePores ?
                new PoresParameters
                {
                    PdbId = computation.PdbId,
                    WorkingDirectory = Path.Combine(computation.SubmitDirectory(0)),
                    InMembrane = inMembraneMode,
                    Chains = chains,
                    PyMolLocation = Config.PyMOL,
                    MemEmbedLocation = Config.MEMBED,
                    IsBetaBarel = isBetaStructure
                } :
                  new PoresParameters
                  {
                      UserStructure = Directory.GetFiles(Path.Combine(Config.WorkingDirectory, computation.ComputationId)).First(x => Path.GetExtension(x) != ".json"),
                      WorkingDirectory = Path.Combine(computation.SubmitDirectory(0)),
                      InMembrane = inMembraneMode,
                      Chains = chains,
                      PyMolLocation = Config.PyMOL,
                      MemEmbedLocation = Config.MEMBED,
                      IsBetaBarel = isBetaStructure
                  };

            File.WriteAllText(input, JsonConvert.SerializeObject(json, Formatting.Indented));

            try
            {
                Task.Run(() => RunPores(computation, input));
            }
            catch (Exception e)
            {
                computation.ChangeStatus(ComputationStatus.Error, e.Message);
            }

            return computation.GetComputationReport();
        }



        private void RunPores(Computation computation, string inputJson)
        {
            ProcessStartInfo info = new ProcessStartInfo()
            {
                FileName = Config.Pores,
                Arguments = inputJson,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            RunComputation(computation, info);
            //TODO
        }



        internal ComputationReport PrepareAndRunMole(Computation c, ComputationParameters param)
        {            
            c.ChangeStatus(ComputationStatus.Running);

            var xmlPath = BuildXML(c, param);
            File.WriteAllText(Path.Combine(c.SubmitDirectory(c.ComputationUnits.Count), MoleApiFiles.MoleParams), JsonConvert.SerializeObject(param, Formatting.Indented));

            Task.Run(() => RunMole(c, param, xmlPath));

            return c.GetComputationReport();
        }



        private void RunMole(Computation c, ComputationParameters param, string xmlPath)
        {
            ProcessStartInfo info = new ProcessStartInfo()
            {
                FileName = Config.MOLE,
                Arguments = xmlPath,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            RunComputation(c, info);

        }



        private void RunComputation(Computation c, ProcessStartInfo info)
        {
            StringBuilder sb = new StringBuilder();

            Process p = new Process();
            p.StartInfo = info;
            p.EnableRaisingEvents = true;
            p.ErrorDataReceived += (s, e) =>    
            {
                if (e.Data != null) sb.AppendLine(e.Data);
            };

            p.Exited += (s, e) =>
            {
                lock (locker) RunningProcesses.Remove(c.ComputationId);

                if (((Process)s).ExitCode == -1) return;

                if (sb.Length != 0)
                {
                    c.ChangeStatus(ComputationStatus.Error, sb.ToString());
                    return;
                }

                Utils.Extensions.ZipDirectories(Path.Combine(Config.WorkingDirectory, c.ComputationId, c.GetComputationReport().SubmitId.ToString()));
                c.ChangeStatus(ComputationStatus.Finished);
            };


            p.Start();
            RunningProcesses.Add(c.ComputationId, p.Id);
            p.BeginErrorReadLine();
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
            if (RunningProcesses.ContainsKey(cpt.ComputationId))
            {
                try
                {
                    Process.GetProcessById(RunningProcesses[cpt.ComputationId]).Kill();
                    RunningProcesses.Remove(cpt.ComputationId);

                    cpt.ChangeStatus(ComputationStatus.Aborted);
                }
                catch (Exception)
                {
                    return new ComputationReport()
                    {
                        ComputationId = cpt.ComputationId,
                        SubmitId = cpt.ComputationUnits.Count,
                        Status = ComputationStatus.Finished,
                        ErrorMsg = "Something went wrong during termination of the process."
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
            byte[] bytes = null;
            switch (type)
            {

                case "json":
                    bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "json", MoleApiFiles.DataJson));
                    return (bytes, $"mole_channels_{computationId}_{submitId}.json");
                case "report":
                    bytes = File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), MoleApiFiles.Report));
                    return (bytes, $"mole_channels_{computationId}_{submitId}.zip");
                case "molecule":
                    var molecule = Directory.GetFiles(Path.Combine(Config.WorkingDirectory, computationId)).First(x => Path.GetExtension(x) != ".json");
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
                    bytes = Utils.Extensions.ZipDirectory(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "pdb", "profile"));
                    return (bytes, $"mole_channels_{computationId}_{submitId}_pdb.zip");

                default:
                    return (File.ReadAllBytes(Path.Combine(Config.WorkingDirectory, computationId, submitId.ToString(), "json", MoleApiFiles.DataJson)), $"mole_channels_{computationId}_{submitId}.json");
            }


        }
        #endregion



        #region Helpers
        /// <summary>
        /// Builds input XML for complex MOLE computation
        /// </summary>
        /// <param name="c">Parrent where the computation will be executed</param>
        /// <param name="param">Parameters provided by the user.</param>
        /// <returns></returns>
        private string BuildXML(Computation c, ComputationParameters param)
        {
            var structure = Directory.GetFiles(Path.Combine(Config.WorkingDirectory, c.ComputationId)).
                        Where(x => Path.GetExtension(x) != ".json").First();
            var structureName = Path.GetFileNameWithoutExtension(structure);

            var root = new XElement("Tunnels");
            var input = new XElement("Input",
                new XAttribute("SpecificChains", param.Input.SpecificChains),
                new XAttribute("ReadAllModels", param.Input.ReadAllModels ? "1" : "0"), structure);
            var WD = new XElement("WorkingDirectory", Path.Combine(Path.GetDirectoryName(structure), c.GetComputationReport().SubmitId.ToString()));

            var nonActive = new XElement("NonActiveResidues");

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
                    new XAttribute("UseCustomExitsOnly", param.Tunnel.UseCustomExitsOnly ? "1" : "0")));

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
                    new XAttribute("Tunnels", "1"),
                    new XAttribute("PoresAuto", param.PoresAuto ? "1" : "0"),
                    new XAttribute("PoresMerged", param.PoresMerged ? "1" : "0"),
                    new XAttribute("PoresUser", (param.CustomExits?.IsEmpty() ?? true) ? "0" : "1")),
                 new XElement("PyMol",
                    new XAttribute("PDBId", structureName),
                    new XAttribute("SurfaceType", "Spheres")),
                 new XElement("VMD",
                    new XAttribute("PDBId", structureName),
                    new XAttribute("SurfaceType", "Spheres")),
                 new XElement("Chimera",
                    new XAttribute("PDBId", structureName),
                    new XAttribute("SurfaceType", "Spheres")));


            var origins = BuildOriginElement(param.Origin, "Origin");

            root.Add(input);
            root.Add(WD);
            root.Add(nonActive);
            root.Add(par);
            root.Add(export);
            root.Add(origins);

            if (!param.CustomExits?.IsEmpty() ?? false)
            {
                var customExits = BuildOriginElement(param.CustomExits, "CustomExit");
            }

            var path = Path.Combine(c.SubmitDirectory(c.GetComputationReport().SubmitId), MoleApiFiles.InputXML);
            root.Save(path);
            return path;
        }


        /// <summary>
        /// Builds Origin element. If null or empty automatic start points are used for calculation.
        /// PatternQuery expression, Residues[] and Points3D[] are all included
        /// </summary>
        /// <param name="o">Origin parameter</param>
        /// <returns>XElement for the MOLE input XML</returns>
        private XElement BuildOriginElement(Origin o, string key)
        {
            if (o == null) return new XElement("Origins", new XAttribute("Auto", "1"));

            var element = new XElement($"{key}s");
            if (o.IsEmpty())
                return new XElement($"{key}s", new XAttribute("Auto", "1"));

            if (!o.Points.IsNullOrEmpty()) element.Add(o.Points.Select(x => new XElement($"{key}s", new XElement("Point", new XAttribute("X", x.X), new XAttribute("Y", x.Y), new XAttribute("Z", x.Z)))));

            if (!o.Residues.IsNullOrEmpty())
            {
                foreach (var item in o.Residues)
                {
                    element.Add(new XElement($"{key}", BuildResiduesElement(item)));
                }
            }

            if (!o.QueryExpresion.IsNullOrEmpty()) element.Add(new XElement($"{key}", new XElement("Query", o.QueryExpresion)));

            return element;
        }


        /// <summary>
        /// Given the list of residues builds their XML notation
        /// </summary>
        /// <param name="residues"></param>
        /// <returns></returns>
        private XElement[] BuildResiduesElement(Residue[] residues) =>
            residues.Select(x => new XElement("Residue", new XAttribute("SequenceNumber", x.SequenceNumber), new XAttribute("Chain", x.Chain))).ToArray();
        #endregion

    }
}
