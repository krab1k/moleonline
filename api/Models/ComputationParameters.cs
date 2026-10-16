using Mole.API.Utils;
using Newtonsoft.Json;

namespace Mole.API.Models;

#region MOLE
public class ComputationParameters
{
    [JsonProperty]
    public Input Input { get; set; }
    [JsonProperty]
    public Cavity Cavity { get; set; } = new Cavity();
    [JsonProperty]
    public Tunnel Tunnel { get; set; }
    [JsonProperty]
    public Residue[] NonActiveResidues { get; set; }
    [JsonProperty]
    public string QueryFilter { get; set; }
    [JsonProperty]
    public Origin Origin { get; set; }
    [JsonProperty]
    public Origin CustomExits { get; set; }
    [JsonProperty]
    public bool PoresMerged { get; set; } = false;
    [JsonProperty]
    public bool PoresAuto { get; set; } = false;


    public ComputationParameters() { }

    [JsonConstructor]
    public ComputationParameters(Input input, Cavity cavity, Tunnel tunnel, Residue[] nonActiveResidue, string queryFilter, Origin origin, Origin customExits, bool poresMerged, bool poresAuto)
    {
        Input = input ?? new Input();
        Cavity = cavity ?? new Cavity();
        Tunnel = tunnel ?? new Tunnel();
        NonActiveResidues = nonActiveResidue ?? [];
        QueryFilter = queryFilter ?? string.Empty;
        Origin = origin ?? new Origin();
        CustomExits = customExits ?? new Origin();
        PoresMerged = poresMerged;
        PoresAuto = poresAuto;


    }
}



public class Input
{
    public string SpecificChains { get; set; } = string.Empty;
    public bool ReadAllModels { get; set; } = false;

}


public class Cavity
{
    public bool IgnoreHETAtoms { get; set; } = false;
    public bool IgnoreHydrogens { get; set; } = false;
    public double InteriorThreshold { get; set; } = 1.1;
    public double ProbeRadius { get; set; } = 5.0;

}



public class Tunnel
{
    public string WeightFunction { get; set; } = "VoronoiScale"; //"VoronoiScale", "LengthAndRadius", "Length", "Constant"
    public double BottleneckRadius { get; set; } = 1.25;
    public double BottleneckTolerance { get; set; } = 3.0;
    public double MaxTunnelSimilarity { get; set; } = 0.7;
    public double OriginRadius { get; set; } = 5.0;
    public double SurfaceCoverRadius { get; set; } = 10.0;

}


#region Origins

public class Origin
{
    public Point3D[] Points { get; set; }
    public string QueryExpression { get; set; }
    public List<Residue[]> Residues { get; set; }

    public bool IsEmpty()
    {
        return Points.IsNullOrEmpty() && String.IsNullOrEmpty(QueryExpression) && Residues.IsNullOrEmpty();
    }
}

public class Point3D
{
    public double X { get; set; }
    public double Y { get; set; }
    public double Z { get; set; }
}

public class Residue
{
    public string Chain { get; set; }
    public int SequenceNumber { get; set; }
}
#endregion

#endregion

/// <summary>
/// pores.json: the input of the pores tool (tools/pores) and the record of what the user submitted.
/// Older computations have UserStructure and MemEmbedLocation instead of Structure and Membrane, the
/// submitted parameters are the same.
/// </summary>
public class PoresParameters
{
    public string Structure { get; set; }
    public string PdbId { get; set; } // OPM entry to use; null when the structure is not the PDB entry as OPM has it
    public string WorkingDirectory { get; set; } // the submit directory, MOLE input and results
    public string Membrane { get; set; } // where the tool writes membrane.json

    public bool InMembrane { get; set; } // the pore only between the membrane planes
    public bool IsBetaBarel { get; set; }
    public string[] Chains { get; set; } = [];
    public double ProbeRadius { get; set; }
    public double InteriorThreshold { get; set; }

    public dynamic UserParameters()
    {
        return new
        {
            InMembrane,
            InteriorThreshold,
            ProbeRadius,
            IsBetaBarel,
            Chains,
        };
    }
}

public class APIPoresParameters {
    public bool InMembrane { get; set; }
    public bool IsBetaBarel { get; set; }
    public string Chains { get; set; }
    public double? InteriorThreshold { get; set; } // null or 0: the default
    public double? ProbeRadius { get; set; }
}
