using System.IO.Compression;

namespace Mole.API.Models;

/// <summary>
/// Active sites per PDB entry from M-CSA (Resources/mcsa_sites.csv.gz, made by tools/mcsa/convert.py): one site per
/// chain, residues by the author's chain and number. /CSA offers them as tunnel starting points.
/// </summary>
public class CSA
{
    private readonly Dictionary<string, CSAResidue[][]> sites;

    /// <param name="path">gzipped CSV "pdb,site,chain,residue,number", sorted by entry and site, sites numbered from 1</param>
    public CSA(string path)
    {
        var entries = new Dictionary<string, List<List<CSAResidue>>>();
        using var reader = new StreamReader(new GZipStream(File.OpenRead(path), CompressionMode.Decompress));
        reader.ReadLine(); // the header
        while (reader.ReadLine() is { } line)
        {
            var fields = line.Split(',');
            if (!entries.TryGetValue(fields[0], out var entry)) entries[fields[0]] = entry = [];
            if (entry.Count < int.Parse(fields[1])) entry.Add([]);
            // a few hundred distinct chains and residue names among about 700,000 residues
            entry[^1].Add(new CSAResidue { Chain = string.Intern(fields[2]), Name = string.Intern(fields[3]), SequenceNumber = int.Parse(fields[4]) });
        }
        sites = entries.ToDictionary(e => e.Key, e => e.Value.Select(site => site.ToArray()).ToArray());
    }

    /// <returns>The active sites of a PDB entry (lower case id), none when M-CSA does not know it</returns>
    public CSAResidue[][] Get(string pdbId) => sites.TryGetValue(pdbId, out var found) ? found : [];
}



public class CSAResidue : Residue
{
    public string Name { get; set; } = "";

    public override string ToString()
    {
        return $"{SequenceNumber} {Chain}";
    }
}
