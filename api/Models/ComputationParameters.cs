using System;
using System.Collections.Generic;
using Mole.API.Utils;
using Newtonsoft.Json;

namespace Mole.API.Models
{
    #region MOLE
    public class ComputationParameters
    {
        [JsonProperty]
        public Input Input { get; set; }
        [JsonProperty]
        public Cavity Cavity { get; set; }
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
            NonActiveResidues = nonActiveResidue ?? (new Residue[0]);
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

    public class PoresParameters
    {
        public bool InMembrane { get; set; } // Decides whether or not the pore will be calculated in the membrane region only. => Valid only in case OPM structure is present
        public bool IsBetaBarel { get; set; }

        public string UserStructure { get; set; } // Path to the input protein
        public string PdbId { get; set; }    // PdbId to be processed e.g. "1tqn"
        public string WorkingDirectory { get; set; }
        public string[] Chains { get; set; } // chains to be processed
        public string PyMolLocation { get; set; }
        public string MemEmbedLocation { get; set; }

        public dynamic UserParameters()
        {
            return new
            {
                InMembrane = InMembrane,
                IsBetaBarel = IsBetaBarel,
                Chains = Chains,
            };
        }
    }


}
