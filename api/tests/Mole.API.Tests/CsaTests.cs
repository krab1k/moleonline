using Mole.API.Models;

namespace Mole.API.Tests;

/// <summary>Active sites from M-CSA (Resources/mcsa_sites.csv.gz), as /CSA serves them for PDB entries</summary>
public class CsaTests
{
    private static readonly CSA Csa = new(Path.Combine(AppContext.BaseDirectory, "Resources", "mcsa_sites.csv.gz"));

    private static string[] Sites(string pdbId) =>
        [.. Csa.Get(pdbId).Select(site => string.Join(" ", site.Select(r => $"{r.Chain}:{r.Name}{r.SequenceNumber}")))];

    [Fact]
    public void One_site_per_chain()
    {
        // M-CSA groups the four copies together; the web places a site at the centre of its residues
        Assert.Equal(["A:Arg39 A:Lys219 A:Gly376 A:Gly399", "B:Arg39 B:Lys219 B:Gly376 B:Gly399",
                      "C:Arg39 C:Lys219 C:Gly376 C:Gly399", "D:Arg39 D:Lys219 D:Gly376 D:Gly399"], Sites("13pk"));
    }

    [Fact]
    public void Descriptions_of_one_site_are_merged()
    {
        // four M-CSA entries describe the trypsin site of each chain with slightly different residues
        Assert.Equal(4, Sites("1a0j").Length);
        Assert.Equal("A:His57 A:Asp102 A:Gln192 A:Gly193 A:Asp194 A:Ser195 A:Gly196", Sites("1a0j")[0]);
    }

    [Fact]
    public void Residues_have_the_names_of_the_structure()
    {
        // M-CSA names a few residues differently in different entries; checked in the structures
        Assert.Contains("C:Cys25", Sites("1nb3")[2]);
        Assert.DoesNotContain("Gln25", string.Join(" ", Sites("1nb3")));
        Assert.Contains("A:Tyr280", Sites("1b8f")[0]);
        Assert.DoesNotContain("280", string.Join(" ", Sites("1gkm"))); // not in the structure
    }

    [Fact]
    public void Entries()
    {
        Assert.Equal(["A:Thr309 A:Phe435 A:Cys442"], Sites("1tqn")); // the example of the documentation
        Assert.Equal(["A:Asp25 A:Thr26 A:Gly27 B:Asp25 B:Thr26 B:Gly27"], Sites("1a30")); // HIV protease: one site of both chains
        Assert.Equal(["A:Glu208 A:His246 B:Tyr109 B:Glu197", "D:Glu208 D:His246 E:Tyr109 E:Glu197"], Sites("1cqj")); // a site of two different chains, per copy
        Assert.Equal(2, Sites("1a50").Length); // tryptophan synthase: the sites of its two subunits, 40 A apart, stay apart
        Assert.Empty(Sites("1crn")); // not an enzyme
    }
}
