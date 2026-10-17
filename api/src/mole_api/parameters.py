"""Parameters of a tunnel computation (Submit/Mole), stored as mole.json; CompInfo returns them to the web. Unknown
fields are ignored (the web sends some, e.g. Tunnel.UseCustomExitsOnly), except in Input: a request asking for all
models (ReadAllModels, no longer supported: only the first model is used) is refused rather than ignored. A null
field has its default."""

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator


class Defaults(BaseModel):
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


class MoleResidue(BaseModel):
    Chain: str = ""
    SequenceNumber: int = 0


class MolePoint(BaseModel):
    X: float = 0
    Y: float = 0
    Z: float = 0


class MoleOrigin(BaseModel):
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
        ] if not low <= value <= high]  # also NaN
        if self.Tunnel.WeightFunction not in WEIGHT_FUNCTIONS:
            errors.append(f"Weight function must be one of {', '.join(WEIGHT_FUNCTIONS)}.")
        return "Invalid parameters. " + " ".join(errors) if errors else None


WEIGHT_FUNCTIONS = ["VoronoiScale", "LengthAndRadius", "Length", "Constant"]
