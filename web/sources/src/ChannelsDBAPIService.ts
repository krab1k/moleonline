import { AnnotationObject, Annotations, CHANNEL_CATEGORIES, ChannelsDBChannels, ChannelsDBData } from "./DataInterface";
import { getJson, isNotFound, isRefused, RequestError } from "./Http";
import type { MessageInfo } from "./Bridge";
import { CommonOptions, DataSources } from "../config/common";
import { customAlphabet } from 'nanoid';

const ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const nanoidAlphaNum = customAlphabet(ALPHABET, 10);

export interface ResidueAnnotation {
    text: string,
    reference: string,
    link: string,
};
export interface ChannelAnnotation {
    id: string,
    channelId?: string,
    name: string,
    description: string,
    reference: string,
    referenceType: string,
    link?: string
};
export interface ChannelsDataResponse {
    channelsAnnotations: Map<string, ChannelAnnotation[]>
    channelsData: ChannelsDBChannels,
};

export interface ProteinDataResponse {
    residueData: Map<string, ResidueAnnotation[]>;
};

// ChannelsDB is one source of channels next to the computed ones: without its answer in time, the session goes on
// without its channels
const TIMEOUT_SECONDS = 15;

export class ApiService {

    private static baseUrl = DataSources.ANNOTATION_API_URL[DataSources.ANNOTATION_API_MODE];

    private static parseChannelsAnnotations(data: AnnotationObject[]) {
        let map = new Map<string, ChannelAnnotation[]>();

        for (let item of data) {
            if (item.Name === void 0) {
                console.log("Found channel annotation without annotation text(Name). Skipping...");
                continue;
            }
            let list: ChannelAnnotation[] = [];
            if (map.has(item.Id)) {
                let l = map.get(item.Id);
                if (l !== void 0) {
                    list = l;
                }
            }
            list.push(
                {
                    id: nanoidAlphaNum(),
                    name: item.Name,
                    description: item.Description,
                    reference: item.Reference,
                    referenceType: item.ReferenceType,
                    link: this.createLink(item.ReferenceType, item.Reference)
                }
            );
            map.set(item.Id, list);
        }

        return map;
    }

    //PUBMEDID vs UniProtId ??? PUBMED není v JSONU vůbec přítomné
    //link pro uniprot používá adresu http://www.uniprot.org/uniprot/
    public static createLink(type: string, reference: string): string {
        if (type === "DOI") {
            return `https://doi.org/${reference}`;
        }
        else if (type === "UniProt") {
            return `https://www.uniprot.org/uniprot/${reference}`;
        }
        else if (type === "PubMed") {
            return `https://europepmc.org/abstract/MED/${reference}`;
        }
        else {
            if (CommonOptions.DEBUG_MODE)
                console.log(`Unknown reference type ${type} for reference ${reference}`);
            return `#unknown-reference-type`;
        }
    }

    private static parseResidueItem(item: Annotations.Annotation, map: Map<string, ResidueAnnotation[]>) {
        let residueId = `${item.Id} ${item.Chain}`;
        let annotations = map.get(residueId);
        if (annotations === void 0) {
            annotations = [];
        }
        annotations.push({
            text: item.Text,
            reference: item.Reference,
            link: this.createLink(item.ReferenceType, item.Reference),
        });
        map.set(`${item.Id} ${item.Chain}`, annotations);
    }

    private static parseResidueData(data: Annotations.ResidueAnnotations) {
        let map = new Map<string, ResidueAnnotation[]>();

        for (let item of data.ChannelsDB) {
            this.parseResidueItem(item, map);
        }

        for (let item of data.UniProt) {
            this.parseResidueItem(item, map);
        }

        return map;
    }

    public static getChannelsData(pdbid: string): Promise<ChannelsDataResponse> {
        let url = `${this.baseUrl}/channels/pdb/${pdbid}`;
        return getJson(url, TIMEOUT_SECONDS).then((val: ChannelsDBData) => ({
            channelsAnnotations: this.parseChannelsAnnotations(val.Annotations),
            channelsData: val.Channels,
        }));
    }

    public static getProteinData(pdbid: string): Promise<ProteinDataResponse> {
        let url = `${this.baseUrl}/annotations/pdb/${pdbid}`;
        return getJson(url, TIMEOUT_SECONDS).then(val => ({ residueData: this.parseResidueData(val.ResidueAnnotations) }));
    }
}

// No answer, no answer in time (status 0), a server error, or one that may pass (408, 429): ChannelsDB may answer later
function unavailable(reason: unknown): boolean {
    return reason instanceof TypeError || (reason instanceof RequestError && !isRefused(reason));
}

// The messages for the user about ChannelsDB's channels and residue annotations of an entry: what happened, and that
// the channels computed here do not depend on it. None when ChannelsDB answered with channels and annotations.
export function channelsDBNotices(pdbId: string, channels: PromiseSettledResult<ChannelsDBChannels>,
                                  residues: PromiseSettledResult<unknown>): MessageInfo[] {
    const entry = pdbId.toUpperCase();
    const notices: MessageInfo[] = [];
    const residuesUnavailable = residues.status === "rejected" && unavailable(residues.reason);
    if (channels.status === "fulfilled") {
        if (!CHANNEL_CATEGORIES.some(category => (channels.value[category]?.length ?? 0) > 0)) {
            notices.push({ messageType: "Info", message: `ChannelsDB has no channels for ${entry}.` });
        }
    } else if (isNotFound(channels.reason)) {
        notices.push({ messageType: "Info", message: `ChannelsDB has no channels for ${entry}.` });
    } else if (unavailable(channels.reason)) {
        const what = residuesUnavailable ? "its channels and residue annotations" : "its channels";
        notices.push({
            messageType: "Warning",
            message: `ChannelsDB is not available at the moment, ${what} for ${entry} are not shown. Channels can be `
                + `computed here as usual; reload the page later to see ChannelsDB's.`
        });
    } else if (channels.reason instanceof RequestError) {
        notices.push({
            messageType: "Warning",
            message: `ChannelsDB refused to give its channels for ${entry} (HTTP ${channels.reason.status}). Channels can `
                + `be computed here as usual.`
        });
    } else {
        notices.push({
            messageType: "Warning",
            message: `ChannelsDB's channels for ${entry} could not be shown (${channels.reason}). Channels can be computed `
                + `here as usual.`
        });
    }
    // the residue annotations: said with the channels when ChannelsDB is not available; none for an entry ChannelsDB
    // does not have is no news
    if (residues.status === "rejected" && !isNotFound(residues.reason)
        && !(residuesUnavailable && channels.status === "rejected" && unavailable(channels.reason))) {
        notices.push({
            messageType: "Warning",
            message: residuesUnavailable
                ? `ChannelsDB is not available at the moment, its residue annotations for ${entry} are not shown.`
                : `ChannelsDB's residue annotations for ${entry} could not be shown (${residues.reason}).`
        });
    }
    return notices;
}
