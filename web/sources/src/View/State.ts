/*
 * Copyright (c) 2016 - now David Sehnal, licensed under Apache 2.0, See LICENSE file for more info.
 */
import { DataProxyCSAResidues, JobStatus, SessionInfo, SessionStatus } from "../DataProxy";
import { ChannelsDBChannels, ChannelsDBData, firstNonEmptyCategory, Membrane, MembranePoint, MoleData, Origins, Tunnel, TunnelMetaInfo } from "../DataInterface";
import { membraneCandidates } from "./CommonUtils/Misc";
import { ChannelsDBData as ChannelsDBDataCache, LastVisibleChannels } from "../Cache";
import { getParameters } from "../Common/Util/Router";
import { ApiService, CSAResidues } from "../MoleAPIService";
import { isNotFound } from "../Http";
import { Context } from "./Context";
import { CommonOptions } from "../../config/common";
import { Residues } from "./CommonUtils/Residues";
import { ParseJson, RawData } from "molstar/lib/mol-plugin-state/transforms/data";
import { UUID } from "molstar/lib/mol-util";
import { Color } from "molstar/lib/mol-util/color";
import { PluginCommands } from "molstar/lib/mol-plugin/commands";
import { ColorGenerator } from "molstar/lib/extensions/meshes/mesh-utils";
import { Shape } from "molstar/lib/mol-model/shape";
import { Colors, Enum } from "../StaticData";
import { ColorBound } from "./VizualizerMol/color-tunnels/property-color";
import { Property } from "./VizualizerMol/color-tunnels/property-color";
import { LayerColors } from "./CommonUtils/LayerColors";
import { TwoDProtsBridge } from "./CommonUtils/TwoDProtsBridge";
import { TunnelsId } from "./CommonUtils/TunnelsId";
import { StateSelection } from "molstar/lib/mol-state";
import { emit } from "./CommonUtils/Events";

export async function showDefaultVisualsNewSubmission(channels: ChannelsDBChannels, submitId: string) {
    const first = firstNonEmptyCategory(channels);
    if (first !== null) {
        await showChannelDefaultVisuals((first.tunnels.slice(0, 5)) as (Tunnel & TunnelMetaInfo)[], first.channelsDB, submitId);
    }
}

export async function showDefaultVisuals(currentSubmitId: number, submissionChannels: Map<number, ChannelsDBChannels>) {
    const submissionData = submissionChannels.get(currentSubmitId)
    if (submissionData !== undefined) {
        const first = firstNonEmptyCategory(submissionData);
        if (first !== null) {
            await showChannelDefaultVisuals((first.tunnels.slice(0, 5)) as (Tunnel & TunnelMetaInfo)[], first.channelsDB, currentSubmitId === -1 ? "ChannelsDB" : currentSubmitId === -2 ? 'FromFile' : currentSubmitId.toString());
            return;
        }
    }

    for (const submitId of Array.from(submissionChannels.keys())) {
        const data = submissionChannels.get(submitId)
        if (data === undefined) continue;
        const first = firstNonEmptyCategory(data);
        if (first !== null) {
            await showChannelDefaultVisuals((first.tunnels.slice(0, 5)) as (Tunnel & TunnelMetaInfo)[], first.channelsDB, submitId === -1 ? "ChannelsDB" : submitId === -2 ? 'FromFile' : submitId.toString());
            return;
        }
    }

    // nothing to show
}

export async function removeChannelsData(channels?: boolean) {
    const plugin = Context.getInstance().plugin;
    const refs: string[] = [];

    const c = channels !== undefined && channels

    plugin.state.data.cells.forEach((cell, key) => {
        if (cell.obj && ((cell.obj.tags && (cell.obj.tags.includes('Origins') || cell.obj.tags.includes('Surface'))))) { //TODO add this if you want remove also channels: cell.obj.type.name === "Tunnel Entry" || 
            refs.push(key);
        }
        if (c && cell.obj && cell.obj.type.name === "Tunnel Entry") {
            refs.push(key);
        }
    })
    refs.forEach(async ref => {
        await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref });
    })
    await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: 'mole-data-raw' });
    await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: 'mole-data' });
}

interface Point { X: number, Y: number, Z: number };
function residuesToPoints(residueOrigins: CSAResidues): string {
    let points: Point[] = [];

    for (let origin of residueOrigins) {
        let centerOfMass = Residues.getCenterOfMass(origin);
        if (centerOfMass === null) {
            continue;
        }

        points.push(centerOfMass);
    }
    return JSON.stringify({
        Origins: {
            CSAOrigins: {
                Points: points,
                Type: "CSA Origins"
            }
        }
    });
}

function createCSAOriginsData(computationId: string) {
    return new Promise<any>((res, rej) => {
        DataProxyCSAResidues.DataProvider.get(computationId, (compId, info) => {
            try {
                let originsData: string = residuesToPoints(info);
                const update = Context.getInstance().plugin.build();
                update
                    .toRoot()
                    .apply(RawData, { data: originsData }, { ref: 'csa-origins-raw', state: { isGhost: true } })
                    .apply(ParseJson, {}, { ref: 'csa-origins', state: { isGhost: true } })
                    .commit()
                    .then((s) => {
                        res(s)
                    })
                    .catch(error => { rej(error) });
            } catch (e) {
                console.error(e);
                rej(e);
            }
        }, false, (_compId, error) => {
            // the CSA origins are optional: load the rest without them
            console.warn(`CSA origins not available: ${error}`);
            res(null);
        })
    });
}

function generateCaverTag(tunnels: Tunnel[]) {
    for (let idx = 0; idx < tunnels.length; idx++) {
        tunnels[idx].Caver = true;
    }
    return tunnels;
}


export function addCaverTag(moleData: ChannelsDBChannels) {
    moleData.CSATunnels_Caver = moleData.CSATunnels_Caver ? generateCaverTag(moleData.CSATunnels_Caver) : [];
    moleData.ReviewedChannels_Caver = moleData.ReviewedChannels_Caver ? generateCaverTag(moleData.ReviewedChannels_Caver) : [];
    moleData.CofactorTunnels_Caver = moleData.CofactorTunnels_Caver ? generateCaverTag(moleData.CofactorTunnels_Caver) : [];
    moleData.TransmembranePores_Caver = moleData.TransmembranePores_Caver ? generateCaverTag(moleData.TransmembranePores_Caver) : [];
    moleData.ProcognateTunnels_Caver = moleData.ProcognateTunnels_Caver ? generateCaverTag(moleData.ProcognateTunnels_Caver) : [];
    moleData.AlphaFillTunnels_Caver = moleData.AlphaFillTunnels_Caver ? generateCaverTag(moleData.AlphaFillTunnels_Caver) : [];

    return moleData
}

function generateGuidMole(moleData: MoleData) {
    moleData.Channels.MergedPores = TunnelsId.generateGuid(moleData.Channels.MergedPores);
    moleData.Channels.Paths = TunnelsId.generateGuid(moleData.Channels.Paths);
    moleData.Channels.Pores = TunnelsId.generateGuid(moleData.Channels.Pores);
    moleData.Channels.Tunnels = TunnelsId.generateGuid(moleData.Channels.Tunnels);

    return moleData
}

// The membrane of the first candidate that has one (membraneCandidates); the session read again, the cached one may
// miss a job finished on this page
async function membraneData(computationId: string, submitId: number): Promise<MembranePoint[] | null> {
    const jobs = (await ApiService.getSession(computationId)).jobs;
    for (const job of membraneCandidates(jobs, submitId)) {
        try {
            return await ApiService.getMembraneData(computationId, job);
        } catch (err) {
            if (!isNotFound(err)) { // 404: no membrane, e.g. the pores tool failed
                throw err;
            }
        }
    }
    return null;
}

async function downloadMembraneData(computationId: string, submitId: number) {
    return new Promise<any>((res, rej) => {
        membraneData(computationId, submitId).then((data) => {
            if (data === null) {
                res(null);
                return;
            }
            const update = Context.getInstance().plugin.build();
            update
                .toRoot()
                .apply(RawData, { data: JSON.stringify(data) }, { ref: 'membrane-data-raw', state: { isGhost: true } })
                .apply(ParseJson, {}, { ref: 'membrane-data', state: { isGhost: true } })
                .commit()
                .then((s) => {
                    res(s);
                })
                .catch((err) => {
                    rej(err);
                });
        }).catch(err => {
            console.error("Membrane data not available!", err);
            res(null);
        });
    }).then(() => {
    });
}

export async function downloadChannelsData(computationId: string, submitId: number) {
    await removeChannelsData();
    return new Promise<any>((res, rej) => {
        // the session's page (0) shows what the load found, a job's page the job's channels
        (submitId === 0 ? ApiService.getSessionData(computationId) : ApiService.getJobData(computationId, submitId)).then((data) => {
            let update = Context.getInstance().plugin.build();
            update
                .toRoot()
                .apply(RawData, { data }, { ref: 'mole-data-raw', state: { isGhost: true } })
                .apply(ParseJson, {}, { ref: 'mole-data', state: { isGhost: true } })
                .commit()
                .then((s) => {
                    if (Object.keys(s.data).length === 0) {
                        rej('Data not available.');
                    } else {
                        let data_ = s.data as MoleData;
                        data_ = generateGuidMole(data_);
                        res(null);
                    }
                })
                .catch((error) => {
                    rej(error);
                })
        })
            .catch(err => {
                if (isNotFound(err)) {
                    res(null); // no channels: the submission has no results
                } else {
                    rej(err);
                }
            })
    });
}

async function downloadChannelsDBData(computationId: string) {
    await removeChannelsData();
    return new Promise<any>((res, rej) => {
        SessionInfo.get(computationId).then(val => {
            if (val.pdb_id === null) {
                rej('ChannelsDB has channels only for PDB entries, not for an uploaded structure.');
                return;
            }
            ChannelsDBDataCache.getChannelsData(val.pdb_id).then(data => {
                let update = Context.getInstance().plugin.build();
                update
                    .toRoot()
                    .apply(RawData, { data: JSON.stringify({ Channels: data }) }, { ref: 'mole-data-raw', state: { isGhost: true } })
                    .apply(ParseJson, {}, { ref: 'mole-data' })
                    .commit()
                    .then((s) => {
                        if (Object.keys(s.data).length === 0) {
                            rej('Data not available.');
                        } else {
                            let data_ = s.data as ChannelsDBData;
                            data_.Channels = TunnelsId.generateGuidAll(data_.Channels);
                            data_.Channels = TunnelsId.generateIdAll(data_.Channels, computationId, 'channelsDb');
                            data_.Annotations = [];
                            res(null)
                        }
                    }).catch(error => { rej(error); console.error(error) });
            }).catch(err => { rej(err); console.error(err) })
        }).catch(err => { rej(err); console.error(err) })
    });
}

function downloadProteinData(computationId: string) {
    return new Promise<any>(async (res, rej) => {
        try {
            const p = await SessionInfo.get(computationId);
            await Context.getInstance().load(ApiService.structureUrl(computationId), false, p.assembly_id)
                .then((data) => {
                    res(data);
                })
                .catch(error => rej(error));
        } catch (error) {
            console.error(error);
            rej(error);
        }
    });
}

export function loadData(channelsDB: boolean) {
    const context = Context.getInstance();
    //context.plugin.clear(); //TODO: MAYBE YES MAYBE NOT
    if (CommonOptions.DEBUG_MODE)
        console.profile("loadData");

    let modelLoadPromise = new Promise<any>((res, rej) => {
        let parameters = getParameters();

        if (parameters === null) {
            rej("Corrupted url found - cannot parse parameters.");
            return;
        }

        let computationId = parameters.computationId;
        let submitId = parameters.submitId;
        if (submitId === 0) {
            // the session itself: its structure, with what its load found, once loaded
            SessionStatus.waitUntilLoaded(computationId).then(session => {
                if (session.state === "failed") {
                    rej(session.error.trim() || "The structure could not be loaded.");
                    return;
                }
                const proteinLoaded = context.plugin.state.data.tree.children.has('protein-data');
                acquireData(computationId, 0, res, rej, !proteinLoaded, true, channelsDB);
            }, rej);
            return;
        }
        if (CommonOptions.DEBUG_MODE)
            console.log("Status watcher - BEFORE EXEC");
        // only the job has no results (failed, cancelled, no such job): the session's structure (0), then why
        const structureThen = (reason: string) => {
            const proteinLoaded = context.plugin.state.data.tree.children.has('protein-data');
            acquireData(computationId, 0, () => rej(reason), rej, !proteinLoaded, false, false);
        };
        JobStatus.Watcher.registerOnChangeHandler(computationId, submitId, (job => {
            if (CommonOptions.DEBUG_MODE)
                console.log("Watcher iteration", job.state);
            if (job.state === "finished") {
                const proteinLoaded = context.plugin.state.data.tree.children.has('protein-data');
                acquireData(computationId, submitId, res, rej, !proteinLoaded, true, channelsDB);
            }
            else if (job.state === "failed") {
                structureThen(job.error.trim() || `Submission #${submitId} failed.`);
            }
            else if (job.state === "cancelled") {
                structureThen(`Submission #${submitId} was stopped, it has no results.`);
            }
            // queued, running: wait for the next state
        }), (err) => {
            if (isNotFound(err)) {
                structureThen(`Submission #${submitId} does not exist.`);
            } else {
                rej(err); // deleted (410), or the API cannot be reached
            }
        });
    })

    // no structure after all: whoever waits for its chains goes on (resolving them again later is a no-op)
    modelLoadPromise.catch(() => context.noStructure());

    let promises = [];

    promises.push(modelLoadPromise);

    return Promise.all(promises);
}

function acquireData(computationId: string, submitId: number, res: any, rej: any, protein: boolean, channels: boolean, channelsDB: boolean) {
    let promises: any[] = [];

    if (protein) {
        if (CommonOptions.DEBUG_MODE)
            console.log("reloading protein structure");
        let proteinAndCSA = new Promise<any>((res, rej) => {
            downloadProteinData(computationId)
                .then(() => {
                    if (CommonOptions.DEBUG_MODE)
                        console.log("reloading CSA Origins");
                    createCSAOriginsData(computationId)
                        .then((data) => res(data))
                        .catch((err) => rej(err));
                })
                .catch(error => rej(error));
        });

        promises.push(proteinAndCSA);
    }
    if (channels && !channelsDB) {
        promises.push(downloadMembraneData(computationId, submitId));
        if (CommonOptions.DEBUG_MODE)
            console.log("reloading channels");
        promises.push(downloadChannelsData(computationId, submitId));
    }
    if (channelsDB) {
        promises.push(downloadChannelsDBData(computationId));
    }

    Promise.all(promises)
        .then(() => {
            res();
            if (CommonOptions.DEBUG_MODE)
                console.profileEnd();
        })
        .catch((error) => {
            console.error(error);
            rej(error);
        })
}

function getSurfaceColorByType(type: string) {
    switch (type) {
        default: return Colors.get(Enum.DefaultColor);
    }
}

async function showSurfaceVisuals(elements: any[], visible: boolean, type: string, label: (e: any) => string, alpha: number): Promise<any> {
    const context = Context.getInstance();

    for (let element of elements) {
        if (!element.__id) element.__id = UUID.create22();
        if (!!element.__isVisible === visible) continue;

        element.__isVisible = visible;
        if (element.__color === void 0) {
            element.__color = getSurfaceColorByType(element.Type);
        }
        if (!visible) {
            if (element.__refBoundary)
                await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: element.__refBoundary });
            if (element.__refInner) {
                await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: element.__refInner });
            }
        } else if (type === "Cavity" && (!!element.Mesh.Boundary || !!element.Mesh.Inner)) {
            const refBoundary = await context.renderSurface(element.Mesh.Boundary.Triangles, element.Mesh.Boundary.Vertices, Colors.get(Enum.CavityBoundary), undefined, `<b>${element.Type} ${element.Id}</b>, Volume: ${Math.round(element.Volume)} Å`);
            const refInner = await context.renderSurface(element.Mesh.Inner.Triangles, element.Mesh.Inner.Vertices, Colors.get(Enum.CavityInner), 'InnerCavity'); //TODO change color

            element.__refBoundary = refBoundary?.ref;
            element.__refInner = refInner?.ref;

        }
        else {
            const ref = await context.renderSurface(element.Mesh.Boundary.Triangles, element.Mesh.Boundary.Vertices, Colors.get(Enum.CavityBoundary), undefined, `<b>${element.Type} ${element.Id}</b>, Volume: ${Math.round(element.Volume)} Å`);
            element.__refBoundary = ref?.ref;
            element.__refInner = null;
        }
    }
    for (let element of elements) {
        element.__isBusy = false;
    }
    return new Promise<any>((res, rej) => { res(null) });
}

// Loads the data of the computation in the URL into the viewer. Resolves with the MOLE data to list, the CSA origins
// added; rejects with the message for the user.
export async function loadMoleData(channelsDB: boolean): Promise<MoleData> {
    const plugin = Context.getInstance().plugin;
    try {
        await loadData(channelsDB);
    } catch (e) {
        throw (e !== void 0 && e !== null && String(e).length > 0) ? String(e) : 'Application was unable to load data. Please try again later.';
    }
    const entities = plugin.state.data.select(StateSelection.first('mole-data'))[0];
    if (entities?.obj?.data === undefined || Object.keys(entities.obj.data).length === 0) {
        const params = getParameters();
        throw params === null
            ? 'Sorry. Given url is not valid.'
            : `There are no vizualization data for computation '${params.computationId}' and submission '${params.submitId}'. Try to submit some computation job.`;
    }
    const data = entities.obj.data as MoleData;
    const csaOrigins = plugin.state.data.select(StateSelection.first('csa-origins'))[0];
    if (csaOrigins?.obj?.data !== undefined && Object.keys(csaOrigins.obj.data).length > 0) {
        (data as any).Origins ??= {};
        (data as any).Origins.CSAOrigins = csaOrigins.obj.data.Origins.CSAOrigins;
    }
    return data;
}

export function showCavityVisuals(cavities: any[], visible: boolean): Promise<any> {
    return showSurfaceVisuals(cavities, visible, 'Cavity', (cavity: any) => `${cavity.Type} ${cavity.Id}`, 0.33);
}

// the last pending visual operation of each channel
const channelOperations = new WeakMap<Tunnel, Promise<unknown>>();

// Runs the channel's visual operations one after another, so each one starts from the
// channel's state left by the previous one and no rendered visual is left untracked.
function serializeChannelOperation<T>(channel: Tunnel, operation: () => Promise<T>): Promise<T> {
    const previous = channelOperations.get(channel) ?? Promise.resolve();
    const result = previous.catch(() => { }).then(operation);
    channelOperations.set(channel, result);
    return result;
}

/**
 * Resolves after the browser painted the next frame. Hidden pages get no frames, and so do some
 * visible ones (occluded windows), so a hidden page does not wait and a timer ends the wait anyway.
 */
function nextPaint(): Promise<void> {
    if (document.hidden) return Promise.resolve();
    return new Promise(res => {
        const fallback = setTimeout(res, 100);
        requestAnimationFrame(() => setTimeout(() => {
            clearTimeout(fallback);
            res();
        }));
    });
}

// The number of running operations that render channels
let renderingOperations = 0;

/**
 * Rendering a channel blocks the page until its mesh is computed, so the operations rendering
 * channels are announced while any of them runs, and the browser paints before each channel
 * (see renderChannel), letting the page show what is going on.
 */
async function whileRendering<T>(operation: () => Promise<T>): Promise<T> {
    if (renderingOperations++ === 0) emit('channelsRendering', true);
    try {
        return await operation();
    } finally {
        if (--renderingOperations === 0) emit('channelsRendering', false);
    }
}

/** Whether channels are being rendered, for the components mounted meanwhile */
export function isRenderingChannels() {
    return renderingOperations > 0;
}

async function renderChannel<T>(render: () => Promise<T>): Promise<T> {
    await nextPaint();
    return render();
}

function showChannelDefaultVisuals(channels: Tunnel[] & TunnelMetaInfo[], channelsDB: boolean, submitId: string): Promise<any> {
    return whileRendering(() => updateChannelDefaultVisuals(channels, channelsDB, submitId));
}

async function updateChannelDefaultVisuals(channels: Tunnel[] & TunnelMetaInfo[], channelsDB: boolean, submitId: string): Promise<any> {
    const context = Context.getInstance();

    try {
        for (let channel of channels) {
            await serializeChannelOperation(channel, async () => {
                channel.__id = UUID.create22();

                if (channel.__color === void 0) {
                    channel.__color = ColorGenerator.next().value;
                }

                if (channelsDB !== undefined && channelsDB) {
                    channel.__channelsDB = true;
                }

                channel.__submissionId = submitId.toString();
                channel.__layerColored = false;

                const [loci, ref] = await renderChannel(() => context.renderTunnel(channel));

                // only a rendered channel counts as visible, so a failure keeps the channels rendered before it
                channel.__ref = ref;
                channel.__loci = loci as Shape.Loci;
                channel.__isVisible = true;
                LastVisibleChannels.set(channel);
                LayerColors.invokeOnChannelAdd(channel.__ref);
            });
        }
    } finally {
        for (let channel of channels) {
            channel.__isBusy = false;
        }
    }
}

export function showChannelVisuals(channels: Tunnel[] & TunnelMetaInfo[], visible: boolean, forceRepaint?: boolean, channelsDB?: boolean, submitId?: string): Promise<any> {
    const update = () => updateChannelVisuals(channels, visible, forceRepaint, channelsDB, submitId);
    return visible ? whileRendering(update) : update();
}

async function updateChannelVisuals(channels: Tunnel[] & TunnelMetaInfo[], visible: boolean, forceRepaint?: boolean, channelsDB?: boolean, submitId?: string) {
    try {
        for (let channel of channels) {
            await serializeChannelOperation(channel, () => updateChannelVisual(channel, visible, forceRepaint, channelsDB, submitId));
        }
    } finally {
        for (let channel of channels) {
            channel.__isBusy = false;
        }
    }
}

async function updateChannelVisual(channel: Tunnel & TunnelMetaInfo, visible: boolean, forceRepaint?: boolean, channelsDB?: boolean, submitId?: string) {
    const context = Context.getInstance();

    if (!channel.__id) channel.__id = UUID.create22();
    if (!!channel.__isVisible === visible && !forceRepaint) return;

    if (channel.__color === void 0) {
        // channel.__color = ColorScheme.Colors.getRandomUnused(); // TODO
        channel.__color = ColorGenerator.next().value;
    }
    if (!channel.__submissionId && submitId) {
        channel.__submissionId = submitId;
    }

    if (channelsDB !== undefined && channelsDB) {
        channel.__channelsDB = true;
    }

    // the channel's state changes only once the plugin has done so, so a failed channel keeps its previous state
    if (!visible) {
        await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: channel.__ref });
        channel.__isVisible = false;
        channel.__layerColored = false;
        LayerColors.invokeOnChannelRemoved(channel.__ref);
        LastVisibleChannels.remove(channel);
    } else {
        // a repainted channel keeps its old visual until the new one is rendered
        const previousRef = channel.__ref;
        const [loci, ref] = await renderChannel(() => context.renderTunnel(channel));
        channel.__ref = ref;
        channel.__loci = loci as Shape.Loci;
        channel.__isVisible = true;
        channel.__layerColored = false;
        LastVisibleChannels.set(channel);

        if (forceRepaint !== void 0 && forceRepaint) {
            await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: previousRef });
        }
        LayerColors.invokeOnChannelAdd(channel.__ref);
    }

    const svgElement = document.getElementById('svgContainer');
    const elementId = TwoDProtsBridge.getFromIdTable(channel.Id)
    const element = document.getElementById(`${elementId}`)
    if (svgElement && element) {
        const targetElement = svgElement.querySelector<SVGGElement>(`g#${CSS.escape(`${elementId}`)}`);
        if (targetElement) {
            if (visible) {
                targetElement.removeAttribute("display");
                targetElement.setAttribute('style', `fill: ${Color.toHexStyle(channel.__color)}; stroke: ${Color.toHexStyle(channel.__color)}; opacity: 1`);
                targetElement.dataset.selectOriginalFill = Color.toHexStyle(channel.__color); 
                targetElement.dataset.hoverOriginalFill = Color.toHexStyle(channel.__color);
            } else {
                targetElement.setAttribute("display", "none");
            }
        }
    }
}

export function showChannelPropertyColorVisuals(channel: Tunnel & TunnelMetaInfo, colorOptions: { property: Property, colorBounds: ColorBound }, forceRepaint?: boolean, channelsDB?: boolean): Promise<any> {
    return whileRendering(() => updateChannelPropertyColorVisual(channel, colorOptions, forceRepaint, channelsDB));
}

async function updateChannelPropertyColorVisual(channel: Tunnel & TunnelMetaInfo, colorOptions: { property: Property, colorBounds: ColorBound }, forceRepaint?: boolean, channelsDB?: boolean) {
    const context = Context.getInstance();

    try {
        await serializeChannelOperation(channel, async () => {
            if (!channel.__id) channel.__id = UUID.create22();
            if (!channel.__isVisible && !forceRepaint) return;

            if (channel.__color === void 0) {
                // channel.__color = ColorScheme.Colors.getRandomUnused(); // TODO
                channel.__color = ColorGenerator.next().value;
            }

            if (channelsDB !== void 0 && channelsDB) {
                channel.__channelsDB = true;
            }

            // a coloured channel only takes the new colours: its surface does not depend on the property
            if (channel.__layerColored && channel.__isVisible && await context.recolorPropertyColorTunnel(channel.__ref)) return;

            // the channel keeps its old visual and state until the new visual is rendered
            const previousRef = channel.__ref;
            const [loci, ref] = await renderChannel(() => context.renderPropertyColorTunnel(channel, colorOptions));
            channel.__ref = ref;
            channel.__loci = loci as Shape.Loci;
            channel.__isVisible = true;
            channel.__layerColored = true;

            if (forceRepaint !== void 0 && forceRepaint) {
                await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: previousRef });
            }
        });
    } finally {
        channel.__isBusy = false;
    }
}

function getOriginColorByType(origins: any) {
    switch (origins.Type as string) {
        case 'Computed': return Colors.get(Enum.ComputedOrigin);
        case 'CSA Origins': return Colors.get(Enum.CSAOrigin);
        default: return Colors.get(Enum.OtherOrigin);
    }
}

export async function showMembraneVisuals(membraneData: Membrane[], visible: boolean): Promise<any> {
    let membranes = [];
    const context = Context.getInstance();

    for (let membrane of membraneData) {
        if (!membrane.__id) membrane.__id = UUID.create22();
        if (!!membrane.__isVisible === visible) continue;

        membrane.__isVisible = visible;
        membranes.push(membrane);
        if (!visible) {
            await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: membrane.__ref });
        } else {
            const ref = await context.renderMembrane(membrane.obj.data);
            membrane.__ref = ref.ref;
        }

    }

    return Promise.resolve().then(() => {
        for (let membrane of membranes) {
            membrane.__isBusy = false;
        }
    });
}

export async function showOriginsSurface(origins: any, visible: boolean): Promise<any> {
    if (!origins.__id) origins.__id = UUID.create22();
    if (!origins.Points.length || !!origins.__isVisible === visible) return Promise.resolve();

    const context = Context.getInstance();

    origins.__isVisible = visible;
    if (origins.__color === void 0) {
        origins.__color = getOriginColorByType(origins);
    }
    if (!visible) {
        await PluginCommands.State.RemoveObject(context.plugin, { state: context.plugin.state.data, ref: origins.__ref });
    } else {
        const ref = await context.renderOrigin(origins.Points, origins.__color, undefined, "Origins", "Origin", origins.Type);
        (origins as Origins).__ref = ref.ref;
    }

    origins.__isBusy = false;
    return Promise.resolve();
}

