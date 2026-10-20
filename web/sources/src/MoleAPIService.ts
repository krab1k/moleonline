import { DataSources } from "../config/common";
import { MembranePoint } from "./DataInterface";
import { deleteEmpty, getJson, getText, postEmpty, postForm, postJson } from "./Http";
import type { components } from "./MoleAPISchema";

// What the API returns, from its OpenAPI description (MoleAPISchema.d.ts, generated: npm run api-types).
// The parameter types below stay the web's own: the forms' data, which the API reads a part of.
type Schemas = components["schemas"];

// A tunnel or pore computation on a session: queued, running, then finished, failed (error says why) or cancelled.
// The API describes the parameters as plain objects; by the job's type they are MOLE's (mole.json) or the pore's.
export type Job = Omit<Schemas["Job"], "type" | "parameters"> & NewJob;
export type JobState = Schemas["JobState"];
export type NewJob = { type: "tunnels", parameters: MoleConfig } | { type: "pores", parameters: PoresConfig };

// A loaded structure, on which jobs run: queued, loading, then ready or failed (error says why)
export type Session = Omit<Schemas["Session"], "jobs"> & { jobs: Job[] };
export type SessionState = Schemas["SessionState"];

// A job no longer queued or running
export function isDone(state: JobState): boolean {
    return state !== "queued" && state !== "running";
}

export interface MoleConfig {
    Input?: MoleConfigInput,
    Cavity?: MoleConfigCavity,
    Tunnel?: MoleConfigTunnel,
    NonActiveResidues?: MoleConfigNonActiveResidues[] | null,
    QueryFilter?: string | null,
    Origin?: MoleConfigOrigin | null,
    CustomExits?: MoleConfigOrigin | null,
    PoresMerged?: boolean,
    PoresAuto?: boolean
};
export interface MoleConfigInput {
    SpecificChains: string
};
export interface MoleConfigCavity {
    IgnoreHETAtoms: boolean,
    IgnoreHydrogens: boolean,
    InteriorThreshold: number,
    ProbeRadius: number
};
export interface MoleConfigTunnel {
    WeightFunction: string,
    BottleneckRadius: number,
    BottleneckTolerance: number,
    MaxTunnelSimilarity: number,
    OriginRadius: number,
    SurfaceCoverRadius: number,
    UseCustomExitsOnly: boolean
};
export type MoleConfigNonActiveResidues = MoleConfigResidue;
export interface MoleConfigOrigin {
    Points: MoleConfigPoint[] | null,
    QueryExpression: string | null,
    Residues: MoleConfigResidue[][] | null
};
export interface MoleConfigPoint {
    X: number,
    Y: number,
    Z: number
};
export interface MoleConfigResidue { Chain: string, SequenceNumber: number, OperatorName: string, Name: string };

// The API answers with every field filled in; a request may leave out the optional ones
export interface PoresConfig {
    InMembrane?: boolean,
    IsBetaBarrel?: boolean,
    Chains?: string[] | null, // chain names as in the structure, e.g. ["A", "A_2"]; null or []: all chains
    InteriorThreshold: number | null, // null: the default (0.8)
    ProbeRadius: number | null // null: the default (13)
};

// Chain names typed as "A, B": one name per comma, blanks dropped
export function parseChains(text: string): string[] {
    return text.split(",").map(c => c.trim()).filter(c => c.length > 0);
}

export function formatChains(chains: string[] | null | undefined): string {
    return (chains ?? []).join(", ");
}

// CSA residues come without an operator: they refer to the chain in any copy of it
export type CSAResidue = Schemas["CSAResidue"];
export type CSAResidues = CSAResidue[][];
export type ResidueName = string;
export type PatternQueryExpression = string;
export type Cofactors = Map<ResidueName, PatternQueryExpression>;
export type MembranePoints = MembranePoint[];

export class ApiService {
    private static baseUrl = DataSources.API_URL;

    // assembly: "preferred" = the entry's preferred (biological) assembly, which the API looks up;
    // "asymmetric" = the asymmetric unit; otherwise the ID of the assembly to load
    public static createSession(pdbId: string, assembly: "preferred" | "asymmetric" | string): Promise<Session> {
        const body: Schemas["NewSession"] = { pdb_id: pdbId, assembly };
        return postJson(`${this.baseUrl}/sessions`, body);
    }

    public static uploadSession(formData: FormData): Promise<Session> {
        return postForm(`${this.baseUrl}/sessions/upload`, formData);
    }

    public static getSession(sessionId: string): Promise<Session> {
        return getJson(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`);
    }

    public static deleteSession(sessionId: string): Promise<void> {
        return deleteEmpty(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}`);
    }

    public static createJob(sessionId: string, job: NewJob): Promise<Job> {
        const body: NewJob = job.type === "tunnels" ? job : {
            type: "pores",
            // exactly these fields: the API refuses unknown ones
            parameters: {
                InMembrane: job.parameters.InMembrane,
                IsBetaBarrel: job.parameters.IsBetaBarrel,
                Chains: job.parameters.Chains,
                InteriorThreshold: job.parameters.InteriorThreshold,
                ProbeRadius: job.parameters.ProbeRadius
            }
        };
        return postJson(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/jobs`, body);
    }

    public static getJob(sessionId: string, number: number): Promise<Job> {
        return getJson(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/jobs/${number}`);
    }

    public static cancelJob(sessionId: string, number: number): Promise<Job> {
        return postEmpty(`${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/jobs/${number}/cancel`);
    }

    public static getChannelsData(computationId: string, submitId: number): Promise<string> {
        // the JSON as text: the viewer parses it itself
        return getText(`${this.baseUrl}/Data/${computationId}?submitId=${submitId}`);
    }

    public static getMembraneData(computationId: string): Promise<MembranePoints> {
        return getJson(`${this.baseUrl}/Data/${computationId}?format=membrane`);
    }

    public static getCSAResidues(computationId: string): Promise<CSAResidues> {
        return getJson(`${this.baseUrl}/CSA/${computationId}`);
    }

    public static getCofactors(): Promise<Cofactors> {
        return getJson(`${this.baseUrl}/inputs/cofactors.json`).then(s => new Map(Object.entries(s)));
    }
}
