using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Mole.API.Models
{
    public class CSA
    {
        public Dictionary<string, List<ActiveSite>> Database { get; set; }

        public CSA(string pathToCSA)
        {
            Database = new Dictionary<string, List<ActiveSite>>();

            var temp = File.ReadAllLines(pathToCSA).
                Skip(1).
                GroupBy(x => x.Substring(0, 4));

            foreach (var protein in temp)
            {
                Database.Add(protein.Key, new List<ActiveSite>());
                var sites = protein.GroupBy(x => x.Split(new char[] { ',' }, StringSplitOptions.RemoveEmptyEntries)[1]);

                foreach (var site in sites)
                {
                    var activeSite = new ActiveSite();
                    foreach (var residue in site)
                    {
                        var r = new CSAResidue(residue);
                        activeSite.Residues.Add(r);
                    }
                    Database[protein.Key].Add(activeSite);
                }
            }
        }
    }



    public class ActiveSite
    {
        public List<CSAResidue> Residues { get; set; }

        public ActiveSite()
        {
            Residues = new List<CSAResidue>();
        }

    }



    public class CSAResidue : Residue
    {
        public string Name { get; set; }

        public CSAResidue(string line)
        {
            var splitted = line.Split(new char[] { ',' }, StringSplitOptions.None);
            Name = splitted[2];
            SequenceNumber = int.Parse(splitted[4]);
            Chain = splitted[3];
        }

        public CSAResidue() { }

        public override string ToString()
        {
            return $"{SequenceNumber} {Chain}";
        }
    }
}
