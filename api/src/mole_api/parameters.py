"""Parameters of a tunnel computation (Submit/Mole), stored as mole.json; CompInfo returns them to the web. Unknown
fields are ignored (the web sends some, e.g. Tunnel.UseCustomExitsOnly), except in Input: a request asking for all
models (ReadAllModels, no longer supported: only the first model is used) is refused rather than ignored. A null
field has its default. Numbers are finite: NaN or infinity would reach MOLE's input (JSON stores them as null)."""

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator


class Parameters(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class Defaults(Parameters):
    @field_validator("*", mode="before")
    @classmethod
    def null_is_default(cls, value, info: ValidationInfo):
        return cls.model_fields[info.field_name].get_default(call_default_factory=True) if value is None else value


class MoleInput(Defaults):
    model_config = ConfigDict(extra="forbid")
    SpecificChains: str = ""


class MoleCavity(Defaults):
    IgnoreHETAtoms: bool = False
    IgnoreHydrogens: bool = False
    InteriorThreshold: float = 1.1
    ProbeRadius: float = 5.0


class MoleTunnel(Defaults):
    WeightFunction: str = "VoronoiScale"
    BottleneckRadius: float = 1.25
    BottleneckTolerance: float = 3.0
    MaxTunnelSimilarity: float = 0.7
    OriginRadius: float = 5.0
    SurfaceCoverRadius: float = 10.0


class MoleResidue(Parameters):
    Chain: str = ""
    SequenceNumber: int = 0


class MolePoint(Parameters):
    X: float = 0
    Y: float = 0
    Z: float = 0


class MoleOrigin(Parameters):
    """Each part is optional"""
    Points: list[MolePoint] | None = None
    QueryExpression: str | None = None
    Residues: list[list[MoleResidue]] | None = None

    def is_empty(self) -> bool:
        return not (self.Points or self.QueryExpression or self.Residues)


class MoleParameters(Defaults):
    Input: MoleInput = MoleInput()
    Cavity: MoleCavity = MoleCavity()
    Tunnel: MoleTunnel = MoleTunnel()
    NonActiveResidues: list[MoleResidue] = []
    QueryFilter: str = ""
    Origin: MoleOrigin = MoleOrigin()
    CustomExits: MoleOrigin = MoleOrigin()
    PoresMerged: bool = False
    PoresAuto: bool = False

    def problems(self) -> str | None:
        """The ranges of the web, so that a request cannot start an unreasonable computation; None when valid"""
        errors = [f"{name} must be between {low:g} and {high:g}." for name, value, low, high in [
            ("Probe radius", self.Cavity.ProbeRadius, 1.4, 45),
            ("Interior threshold", self.Cavity.InteriorThreshold, 0.3, 3),
            ("Origin radius", self.Tunnel.OriginRadius, 0.1, 10),
            ("Surface cover radius", self.Tunnel.SurfaceCoverRadius, 5, 20),
            ("Bottleneck radius", self.Tunnel.BottleneckRadius, 0, 5),
            ("Bottleneck tolerance", self.Tunnel.BottleneckTolerance, 0, 5),
            ("Max tunnel similarity", self.Tunnel.MaxTunnelSimilarity, 0, 1),
        ] if not low <= value <= high]
        if self.Tunnel.WeightFunction not in WEIGHT_FUNCTIONS:
            errors.append(f"Weight function must be one of {', '.join(WEIGHT_FUNCTIONS)}.")
        return "Invalid parameters. " + " ".join(errors) if errors else None


WEIGHT_FUNCTIONS = ["VoronoiScale", "LengthAndRadius", "Length", "Constant"]


class PoresParameters(Defaults):
    """pores.json: the input of the pores tool (mole_api.pores) and the record of what the user submitted. Files of
    earlier versions miss fields (IsBetaBarrel was IsBetaBarel): their defaults."""
    Structure: str = ""  # the structure file of the computation
    PdbId: str | None = None  # OPM entry to use; None when the structure is not the PDB entry as OPM has it
    WorkingDirectory: str = ""  # the submit directory, MOLE input and results
    Membrane: str = ""  # where the tool writes membrane.json
    InMembrane: bool = False  # the pore only between the membrane planes
    IsBetaBarrel: bool = False
    Chains: list[str] = []
    ProbeRadius: float = 0
    InteriorThreshold: float = 0

    def user_parameters(self) -> dict:
        """What the user submitted, as CompInfo reports it"""
        return self.model_dump(include={"InMembrane", "IsBetaBarrel", "Chains", "ProbeRadius", "InteriorThreshold"})


class PoresRequest(Parameters):
    """Body of Submit/Pores. Unknown fields are an error: a misspelled one would otherwise be ignored silently."""
    model_config = ConfigDict(extra="forbid")
    InMembrane: bool = False
    IsBetaBarrel: bool = False
    Chains: list[str] | None = None  # None or empty: all chains
    ProbeRadius: float | None = None  # None: the default
    InteriorThreshold: float | None = None

    def problems(self) -> str | None:
        """None when valid"""
        errors = [f"{name} must be between {low:g} and {high:g}." for name, value, low, high in [
            ("Probe radius", self.ProbeRadius, 1.4, 45),
            ("Interior threshold", self.InteriorThreshold, 0.3, 3),
        ] if value is not None and not low <= value <= high]
        if any(not chain.strip() or any(c.isspace() for c in chain) for chain in self.Chains or []):
            errors.append("Chain names must not be empty or contain spaces.")
        return "Invalid parameters. " + " ".join(errors) if errors else None


DEFAULT_PORES_PROBE_RADIUS, DEFAULT_PORES_INTERIOR_THRESHOLD = 13.0, 0.8
