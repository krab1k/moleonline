using Mole.API.Utils;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
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

    public class ComputationReport
    {
        public string ComputationId { get; set; }
        public int SubmitId { get; set; }
        public string Status { get; set; }
        public string ErrorMsg { get; set; }

        public ComputationReport()
        { }

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

        public Computation()
        {
        }

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
        /// Saves current state of the computation to the ComputationStatus file
        /// </summary>
        /// <param name="baseDir"></param>
        public void SaveStatus() =>
            File.WriteAllText(StatusPath(), JsonConvert.SerializeObject(this, Formatting.Indented));


        internal ComputationReport GetComputationReport(int i = 0)
        {
            var a = i;
            if (i == 0) a = this.ComputationUnits.Count;
            var unit = ComputationUnits.FirstOrDefault(x => x.SubmitId == a);

            if (unit == null)
            {
                return new ComputationReport()
                {
                    ComputationId = ComputationId,
                    SubmitId = i,
                    Status = ComputationStatus.Error,
                    ErrorMsg = $"SubmitId {i} not found."
                };
            }
            else
            {
                return new ComputationReport(this.ComputationId, unit);
            }
        }


        /// <summary>
        /// Returns Path to the ComputationStatus file
        /// </summary>
        /// <param name="baseDir"></param>
        /// <returns></returns>
        private string StatusPath() => Path.Combine(BaseDir, ComputationId, MoleApiFiles.ComputationStatus);





        public void ChangeStatus(string status, string error = "")
        {
            if (ComputationUnits.Last().Status == ComputationStatus.Deleted) return;

            ComputationUnits.Last().ChangeStatus(status, error);
            SaveStatus();
        }



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




        public static ComputationReport NotExists(string computationId)
        {
            return new ComputationReport()
            {
                ComputationId = computationId,
                SubmitId = 0,
                Status = ComputationStatus.Error,
                ErrorMsg = $"ComputationId [{computationId}] does not exists."
            };
        }




        /// <summary>
        /// Downloads Protein Data Bank file from Coordinate Server
        /// </summary>
        /// <param name="url">Url to fetch the file</param>
        /// <param name="id">PDB if of this file</param>
        public void DownloadStructure()
        {
            var url = AssemblyId != null ? $"https://coords.litemol.org/{PdbId}/assembly?id={AssemblyId}" : $"https://coords.litemol.org/{PdbId}/full";

            var file = Path.Combine(BaseDir, ComputationId, PdbId + ".cif");
            using (WebClient cl = new WebClient())
            {
                try
                {
                    cl.DownloadStringCompleted += (s, e) => // parse assembly id to check if it is compatible with DB mode of pore package
                    {
                        var id = XDocument.Parse(e.Result).Root.Elements("assembly").First(x => x.Attribute("prefered").Value.Equals("True")).Attribute("id").Value;
                        if (id == AssemblyId)
                        {
                            this.DbModePores = true;
                        }
                        SaveStatus();
                    };
                    cl.DownloadFileCompleted += (s, e) =>
                    {
                        if (e.Error != null)
                        {
                            ComputationUnits.First().Status = ComputationStatus.FailedInitialization;
                            ComputationUnits.First().ErrorMsg = $"Error downloading PDB structure [{PdbId}]: {e.Error.Message}";
                            SaveStatus();
                        }
                        var error = CheckDownloadedStructure(file);

                        if (String.IsNullOrEmpty(error)) ComputationUnits.First().Status = ComputationStatus.Initialized;
                        else
                        {
                            ComputationUnits.First().Status = ComputationStatus.FailedInitialization;
                            ComputationUnits.First().ErrorMsg = error;
                        }

                        ((WebClient)s).DownloadStringAsync(new Uri($"http://www.ebi.ac.uk/pdbe/static/entry/download/{PdbId}-assembly.xml"));
                        SaveStatus();
                    };

                    cl.DownloadFileAsync(new Uri(url), file);
                    /*using (Stream s = cl.OpenRead(new Uri(url)))
                    {
                        using (GZipStream stream = new GZipStream(s, CompressionMode.Decompress))
                        {
                            Utils.Extensions.ReadStream(file, stream);
                        }

                    }*/

                }
                catch (WebException)
                {
                    ComputationUnits.First().Status = ComputationStatus.FailedInitialization;
                    ComputationUnits.First().ErrorMsg = $"Structure [{PdbId}] is unlikely to exist or has been made obsolete.";
                    SaveStatus();
                }
            }
        }





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




        public string SubmitDirectory(int submitId = 0) => Path.Combine(BaseDir, ComputationId, submitId == 0 ? this.ComputationUnits.Last().SubmitId.ToString() : submitId.ToString());
    }


}
