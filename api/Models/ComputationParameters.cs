using Mole.API.Utils;
using Newtonsoft.Json;

namespace Mole.API.Models;

#region MOLE
public class ComputationParameters
{
    [JsonProperty]
    public Input Input { get; set; } = new();
    [JsonProperty]
    public Cavity Cavity { get; set; } = new Cavity();
    [JsonProperty]
    public Tunnel Tunnel { get; set; } = new();
    [JsonProperty]
    public Residue[] NonActiveResidues { get; set; } = [];
    [JsonProperty]
    public string QueryFilter { get; set; } = "";
    [JsonProperty]
    public Origin Origin { get; set; } = new();
    [JsonProperty]
    public Origin CustomExits { get; set; } = new();
    [JsonProperty]
    public bool PoresMerged { get; set; } = false;
    [JsonProperty]
    public bool PoresAuto { get; set; } = false;


    public ComputationParameters() { }

    [JsonConstructor]
    public ComputationParameters(Input? input, Cavity? cavity, Tunnel? tunnel, Residue[]? nonActiveResidue, string? queryFilter, Origin? origin, Origin? customExits, bool poresMerged, bool poresAuto)
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

    private static readonly string[] WeightFunctions = ["VoronoiScale", "LengthAndRadius", "Length", "Constant"];

    /// <summary>
    /// The ranges of the web, so that a request cannot start an unreasonable computation
    /// </summary>
    /// <returns>What is wrong, null when the parameters are valid</returns>
    public string? Validate()
    {
        List<string> errors = [];
        Limits.Check(errors, "Probe radius", Cavity.ProbeRadius, 1.4, 45);
        Limits.Check(errors, "Interior threshold", Cavity.InteriorThreshold, 0.3, 3);
        Limits.Check(errors, "Origin radius", Tunnel.OriginRadius, 0.1, 10);
        Limits.Check(errors, "Surface cover radius", Tunnel.SurfaceCoverRadius, 5, 20);
        Limits.Check(errors, "Bottleneck radius", Tunnel.BottleneckRadius, 0, 5);
        Limits.Check(errors, "Bottleneck tolerance", Tunnel.BottleneckTolerance, 0, 5);
        Limits.Check(errors, "Max tunnel similarity", Tunnel.MaxTunnelSimilarity, 0, 1);
        if (!WeightFunctions.Contains(Tunnel.WeightFunction)) errors.Add($"Weight function must be one of {string.Join(", ", WeightFunctions)}.");
        return Limits.Message(errors);
    }
}

internal static class Limits
{
    public static void Check(List<string> errors, string name, double value, double min, double max)
    {
        if (!(value >= min && value <= max)) errors.Add($"{name} must be between {min} and {max}."); // also NaN
    }

    public static string? Message(List<string> errors) => errors.Count == 0 ? null : "Invalid parameters. " + string.Join(" ", errors);
}



/// <summary>
/// Only the first model of a structure is used (the web shows only that one). Unknown fields are an error,
/// so a request asking for all models (ReadAllModels, no longer supported) is refused rather than ignored.
/// </summary>
[JsonObject(MissingMemberHandling = MissingMemberHandling.Error)]
public class Input
{
    public string SpecificChains { get; set; } = string.Empty;
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
    public Point3D[]? Points { get; set; } // each part is optional
    public string? QueryExpression { get; set; }
    public List<Residue[]>? Residues { get; set; }

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
    public string Chain { get; set; } = "";
    public int SequenceNumber { get; set; }
}
#endregion

#endregion

/// <summary>
/// pores.json: the input of the pores tool (tools/pores) and the record of what the user submitted
/// </summary>
public class PoresParameters
{
    public string Structure { get; set; } = "";
    public string? PdbId { get; set; } // OPM entry to use; null when the structure is not the PDB entry as OPM has it
    public string WorkingDirectory { get; set; } = ""; // the submit directory, MOLE input and results
    public string Membrane { get; set; } = ""; // where the tool writes membrane.json

    public bool InMembrane { get; set; } // the pore only between the membrane planes
    public bool IsBetaBarrel { get; set; }
    public string[] Chains { get; set; } = [];
    public double ProbeRadius { get; set; }
    public double InteriorThreshold { get; set; }

    /// <summary>
    /// What the user submitted, as CompInfo reports it; the same names and types as PoresRequest
    /// </summary>
    public object UserParameters() => new { InMembrane, IsBetaBarrel, Chains, ProbeRadius, InteriorThreshold };
}

/// <summary>
/// Body of Submit/Pores. Unknown fields are an error, a misspelled one would otherwise be ignored silently
/// </summary>
[JsonObject(MissingMemberHandling = MissingMemberHandling.Error)]
public class PoresRequest
{
    public const double DefaultProbeRadius = 13.0;
    public const double DefaultInteriorThreshold = 0.8;

    public bool InMembrane { get; set; }
    public bool IsBetaBarrel { get; set; }
    public string[]? Chains { get; set; } // null or empty: all chains
    public double? ProbeRadius { get; set; } // null: the default
    public double? InteriorThreshold { get; set; }

    /// <returns>What is wrong, null when the request is valid</returns>
    public string? Validate()
    {
        List<string> errors = [];
        if (ProbeRadius is double probe) Limits.Check(errors, "Probe radius", probe, 1.4, 45);
        if (InteriorThreshold is double threshold) Limits.Check(errors, "Interior threshold", threshold, 0.3, 3);
        if (Chains?.Any(c => string.IsNullOrWhiteSpace(c) || c.Any(char.IsWhiteSpace)) == true) errors.Add("Chain names must not be empty or contain spaces.");
        return Limits.Message(errors);
    }
}
