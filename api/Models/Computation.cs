using Mole.API.Utils;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Xml.Linq;

namespace Mole.API.Models
{
    /// <summary>
    /// Represents a single computation created with submission controller
    /// </summary>
    public class ComputationUnit
    {
        public int SubmitId { get; set; }
        public string Status { get; set; }
        public string ErrorMsg { get; set; }

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
        public string ComputationId { get; set; }
        public int SubmitId { get; set; }
        public string Status { get; set; }
        public string ErrorMsg { get; set; }

        public ComputationReport() { }


        public ComputationReport(string computationId, int id, string status, string errorMsg)
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



    public class Computation
    {
        public string ComputationId { get; set; }
        public string BaseDir { get; set; }
        public bool UserStructure { get; set; }
        public string PdbId { get; set; }
        public string AssemblyId { get; set; }
        public bool DbModePores { get; set; }
        public List<ComputationUnit> ComputationUnits { get; set; }



        public Computation() { }


        /// <summary>
        /// Initialize new computation. Status Initializing, submission Id = 1.
        /// </summary>
        /// <param name="baseDir">Working directory of the API</param>
        /// <param name="pdbId">PDB id to be downloaded</param>
        /// <param name="bioUnit">Optional biological assembly to be downloaded</param>
        public Computation(string baseDir, string pdbId = "", string bioUnit = "")
        {
            ComputationId = Regex.Replace(Convert.ToBase64String(Guid.NewGuid().ToByteArray()), "[/+=]", "");
            ComputationUnits = new List<ComputationUnit>() { ComputationUnit.Initialize() };
            UserStructure = pdbId == string.Empty;
            PdbId = pdbId;
            AssemblyId = bioUnit;
            BaseDir = baseDir;

            Directory.CreateDirectory(Path.Combine(baseDir, ComputationId));

            File.WriteAllText(Path.Combine(baseDir, ComputationId, MoleApiFiles.ComputationStatus), JsonConvert.SerializeObject(this, Formatting.Indented));
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
            if (ComputationUnits.Last().Status == ComputationStatus.Deleted) return;

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
             new ComputationReport(computationId, 0, ComputationStatus.Error, $"ComputationId [{computationId}] does not exists.\n");


        public void DownloadBioStructure()
        {
            AssemblyId = GetBioAssemblyId();
            DownloadStructure();
        }


        /// <summary>
        /// Downloads Protein Data Bank file from Coordinate Server https://www.ebi.ac.uk/pdbe/coordinates
        /// </summary>
        /// <param name="url">Url to fetch the file</param>
        /// <param name="id">PDB if of this file</param>
        public void DownloadStructure()
        {
            var url = AssemblyId != null ? $"https://www.ebi.ac.uk/pdbe/coordinates/{PdbId}/assembly?id={AssemblyId}" : $"https://www.ebi.ac.uk/pdbe/coordinates/{PdbId}/full";

            var file = Path.Combine(BaseDir, ComputationId, PdbId + ".cif");
            using (WebClient cl = new WebClient())
            {
                try
                {
                    cl.DownloadFile(new Uri(url), file);
                    var error = CheckDownloadedStructure(file);
                    if (!String.IsNullOrEmpty(error))
                    {
                        ChangeStatus(ComputationStatus.FailedInitialization, error);
                        return;
                    }
                    DbModePores = AssemblyId == GetBioAssemblyId();
                    SaveStatus();

                }
                catch (WebException e)
                {                    
                    ComputationUnits.First().Status = ComputationStatus.FailedInitialization;
                    ComputationUnits.First().ErrorMsg = $"Structure [{PdbId}] is unlikely to exist or has been made obsolete.";
                    SaveStatus();
                }
            }
        }

        public string GetBioAssemblyId()
        {
            try
            {
                using (WebClient cl = new WebClient())
                {
                    var xml = cl.DownloadString(new Uri($"http://www.ebi.ac.uk/pdbe/static/entry/download/{PdbId}-assembly.xml"));
                    return XDocument.Parse(xml).Root.Elements("assembly").First(x => x.Attribute("prefered").Value.Equals("True")).Attribute("id").Value;
                }
            }
            catch (Exception)
            { return null; }
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
                var temp = errorLine.Substring(36).Trim();
                return temp.Substring(1, temp.Length - 2);
            }
        }



        /// <summary>
        /// Shorthand for SubmitDirectoryPath
        /// </summary>
        /// <param name="submitId"></param>
        /// <returns>Complete path to the submit directory folder</returns>
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
            //using (var waitHandle = new EventWaitHandle(true, EventResetMode.AutoReset, this.ComputationId))
            //{
            //    if (waitHandle.WaitOne())
            //    {
            File.WriteAllText(StatusPath(), JsonConvert.SerializeObject(this, Formatting.Indented));
            File.AppendAllText("apii_log.log", $"save status of {ComputationId} to be {ComputationUnits.Last().Status}");
            //    }
            //    waitHandle.Set();


            //}
        }

    }

}
