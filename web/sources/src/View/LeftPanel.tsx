import React from "react";
import { Context } from "./Context";
import { leftPanelTabs } from "./CommonUtils/Layout";
import { PluginControl } from "./UI/PluginControl/UI";
import { ChannelsControl } from "./UI/PluginControl/ChannelsUI";
import { PluginReactContext } from "molstar/lib/mol-plugin-ui/base";
import { Controls } from "./UI/Controls/UI";
import { getParameters } from "../Common/Util/Router";
import { Events } from "../Bridge";
import { addCaverTag, loadMoleData } from "./State";
import { ChannelsDBChannels, MoleData } from "../DataInterface";
import { SessionInfo } from "../DataProxy";
import { ApiService, Session } from "../MoleAPIService";
import { describeError, isNotFound } from "../Http";
import { ChannelsDBData as ChannelsDBDataCache, TunnelName } from "../Cache"
import { Tunnels } from "./CommonUtils/Tunnels";
import { loadCifTunnels } from "./VizualizerMol/mmcif-tunnels/converter2json";
import { TunnelsId } from "./CommonUtils/TunnelsId";
import { ChannelAnnotation, channelsDBNotices } from "../ChannelsDBAPIService";
import WaveLoader from "./CommonUtils/WaveLoader";
import { LoadingStatusDisplay } from "./CommonUtils/LoadingInfo/LoadingStatusDisplay";
import { LoadingStatus } from "./CommonUtils/LoadingInfo/LoadingStatus";
import { listen } from "./CommonUtils/Events";


export class LeftPanel extends React.Component<{ context: Context }, { isLoading?: boolean, error?: string, data?: any, channelsData: Map<number, ChannelsDBChannels>, compId: string, isLoadingChannels: boolean, channelsDB: boolean }> {
    state = { isLoading: false, isLoadingChannels: false, data: void 0, error: void 0, channelsData: new Map(), compId: "", channelsDB: false };

    componentDidMount() {
        LoadingStatus.setTotalSteps(6);
        leftPanelTabs();
        // from the URL directly: the state set here is not updated yet when reload reads it
        const channelsDB = getParameters()?.isChannelsDB ?? false;
        this.setState({ channelsDB });
        this.reload(channelsDB);
        listen("contentResize", this.onContentResize.bind(this));

        window.addEventListener("resize", (() => {
            this.forceUpdate();
        }).bind(this));

        Events.subscribeToggleLoadingScreen(({ message, visible }) => {
            if (!visible) {
                const uiTabsElement = document.querySelector('#ui-tab');
                if (uiTabsElement) {
                    (uiTabsElement as HTMLElement).click();
                }
            }
        })
    }

    // Runs both loads: structure/visual data and computation info with channels
    // fresh: ask for the computation info again, submissions may have finished since it was loaded
    private reload(channelsDB: boolean, fresh = false) {
        this.setState({ isLoading: true, isLoadingChannels: true, error: void 0 });
        this.load(channelsDB);
        this.loadChannels(fresh);
    }

    private onContentResize() {
        this.forceUpdate();
    }

    // Channels of the finished jobs; the others have no results
    private async loadSubmissionsChannels(session: Session, channels: Map<number, ChannelsDBChannels>) {
        for (const job of session.jobs) {
            if (job.state !== "finished") {
                continue;
            }
            const submitId = job.number;
            let data;
            try {
                data = await ApiService.getJobData(session.id, submitId);
            } catch (e) {
                if (isNotFound(e)) {
                    continue; // no channels after all
                }
                throw e;
            }
            const dataObj = JSON.parse(data) as MoleData;
            let guidData = TunnelsId.generateGuidAll(dataObj.Channels);
            guidData = TunnelsId.generateIdAll(guidData, session.id, submitId.toString());
            TunnelName.reload({ Channels: guidData }, submitId.toString());
            Tunnels.addChannels(submitId.toString(), guidData);
            channels.set(submitId, guidData);
        }
    }

    loadChannels(fresh: boolean) {
        LoadingStatus.log('Loading channels from current session...');
        let params = getParameters();
        if (params === null) {
            this.setState({ isLoading: false, error: `Sorry. Given url is not valid.` });
            LoadingStatus.clear();
            return;
        }
        this.setState({ compId: params.computationId })
        let computationId = params.computationId;
        const channels: Map<number, ChannelsDBChannels> = new Map();

        SessionInfo.DataProvider.get(computationId, ((compId: string, info: Session) => {
            LoadingStatus.log('Processing data from current session...');
            const pdbId = info.pdb_id;
            if (pdbId === null) {
                (async () => {
                    LoadingStatus.log('Parsing data from current session...');
                    const data = await loadCifTunnels(ApiService.structureUrl(computationId));
                    if (data) {
                        const dataObj = { Channels: data.Channels } as MoleData;
                        let guidData = TunnelsId.generateGuidAll(dataObj.Channels);
                        ChannelsDBDataCache.setFileLoadedAnnotations(data.Annotations);
                        const annotations = ChannelsDBDataCache.getFileLoadedAnnotations();
                        const completeChannelsDbData = TunnelsId.generateIdAllWithAnnotations(annotations, guidData, compId, 'file');
                        Tunnels.setChannelsDB(completeChannelsDbData);
                        TunnelName.reload({ Channels: completeChannelsDbData }, '-2');
                        Tunnels.addChannels('-2', completeChannelsDbData);
                        channels.set(-2, completeChannelsDbData);
                    }
                    await this.loadSubmissionsChannels(info, channels);
                    Tunnels.invokeOnTunnelsLoaded();
                    LoadingStatus.log('Finalizing…');
                    this.setState({ channelsData: channels, isLoadingChannels: false })
                })().catch(error => {
                    this.setState({ isLoadingChannels: false, error: String(error) })
                    Events.invokeNotifyMessage({
                        messageType: "Danger",
                        message: describeError(error, 'Could not fetch data from MoleAPI')
                    })
                });
                return;
            }
            // ChannelsDB is one source of channels next to the computed ones: it is waited for (at most its timeout),
            // and whatever it answered, the computed channels are shown and more can be computed
            (async () => {
                const computed: Map<number, ChannelsDBChannels> = new Map();
                const [channelsDb, annotations, residues, submissions] = await Promise.allSettled([
                    ChannelsDBDataCache.getChannelsData(pdbId),
                    ChannelsDBDataCache.getChannelsAnnotations(pdbId),
                    ChannelsDBDataCache.getResidueAnnotations(pdbId),
                    this.loadSubmissionsChannels(info, computed),
                ]);
                if (submissions.status === "rejected") {
                    throw submissions.reason;
                }
                let shown: PromiseSettledResult<ChannelsDBChannels> = channelsDb;
                if (channelsDb.status === "fulfilled") {
                    try {
                        const guidChannelsDbData = TunnelsId.generateGuidAll(channelsDb.value);
                        let completeChannelsDbData = addCaverTag(guidChannelsDbData);
                        const channelAnnotations = annotations.status === "fulfilled" ? annotations.value : new Map<string, ChannelAnnotation[]>();
                        completeChannelsDbData = TunnelsId.generateIdAllWithAnnotations(channelAnnotations, completeChannelsDbData, compId, 'channelsDb');
                        Tunnels.markChannelsDB(completeChannelsDbData);
                        Tunnels.setChannelsDB(completeChannelsDbData);
                        TunnelName.reload({ Channels: completeChannelsDbData }, '-1');
                        channels.set(-1, completeChannelsDbData);
                    } catch (error) {
                        console.error(error);
                        shown = { status: "rejected", reason: error };
                    }
                } else if (!isNotFound(channelsDb.reason)) {
                    console.warn("ChannelsDB:", channelsDb.reason);
                }
                for (const notice of channelsDBNotices(pdbId, shown, residues)) {
                    Events.invokeNotifyMessage(notice);
                }
                computed.forEach((data, submitId) => channels.set(submitId, data));
                Tunnels.invokeOnTunnelsLoaded();
                LoadingStatus.log('Finalizing…');
                this.setState({ channelsData: channels, isLoadingChannels: false })
            })().catch(error => {
                this.setState({ isLoadingChannels: false, error: String(error) })
                Events.invokeNotifyMessage({
                    messageType: "Danger",
                    message: describeError(error, 'Could not fetch data from MoleAPI')
                })
            }).finally(() => LoadingStatus.clear());
        }).bind(this), fresh, (compId: string, error: string) => {
            this.setState({ isLoading: false, isLoadingChannels: false, error });
            LoadingStatus.clear();
        })
    }

    load(channelsDB: boolean) {
        this.setState({ isLoading: true, error: void 0 });
        LoadingStatus.log('Loading channels from ChannelsDB...');
        loadMoleData(channelsDB)
            .then(data => {
                LoadingStatus.log('Processing data from ChannelsDB...');
                this.setState({ isLoading: false, data, error: void 0 });
            })
            .catch(error => {
                this.setState({ isLoading: false, error: String(error), data: void 0 });
                Events.invokeNotifyMessage({
                    messageType: "Danger",
                    message: String(error)
                })
            });
    }


    render() {
        if (!this.state.isLoading && !this.state.isLoadingChannels && !this.state.error) {
            return <div className="d-flex flex-column h-100">
                <div className="tab-content">
                    <div id="ui" className={`toggled tab-pane ${this.state.channelsData.size === 0 ? '' : 'show active'}`} role="tabpanel">
                        <ChannelsControl computationId={this.state.compId} submissions={this.state.channelsData} reload={() => this.reload(this.state.channelsDB, true)}/>
                    </div>
                    <div id="controls" className={`bottom toggled tab-pane flex flex-column ${this.state.channelsData.size === 0 ? 'show active' : ''}`} role="tabpanel">
                        <PluginControl data={this.state.data} />
                        <PluginReactContext.Provider value={this.props.context.plugin}><Controls /></PluginReactContext.Provider>
                    </div>
                </div>
                <div id="left-panel-tabs" className="left-panel-tabs">
                    <ul className="nav nav-tabs flex-column" role="tablist">
                        <li className="nav-item" title="Home"><a id="home-tab" className="nav-link left-panel-tab" href="/" role="tab"><i className="bi bi-house fs-5"></i></a></li>
                        <li className="nav-item"><a style={{ writingMode: "vertical-lr" }} className={`nav-link ${this.state.channelsData.size === 0 ? '' : 'active'} left-panel-tab`} id="ui-tab" data-bs-toggle="tab" data-bs-target="#ui" role="tab" aria-controls="ui" aria-selected="true">Channels</a></li>
                        <li className="nav-item"><a style={{ writingMode: "vertical-lr" }} className={`nav-link ${this.state.channelsData.size === 0 ? 'active' : ''} left-panel-tab`} id="controls-tab" data-bs-toggle="tab" data-bs-target="#controls" role="tab" aria-controls="controls" aria-selected="true">Compute</a></li>
                    </ul>
                    <div id="left-panel-toggle-minimize" className="vertical-toggler">
                        <span className="bi bi-arrows-expand-vertical" />
                    </div>
                </div>
            </div>
        } else {
            let controls: any[] = [];

            if (this.state.isLoading || this.state.isLoadingChannels) {
                controls.push(
                    <div key={'loader'}>
                        <WaveLoader />
                        <LoadingStatusDisplay />
                    </div>);
            } else {
                if (this.state.error) {
                    let error = this.state.error as string | undefined;
                    let errorMessage: string = (error === void 0) ? "" : error;
                    controls.push(
                        <div key={'error'} className="error-message">
                            <div>
                                <b>Data for specified protein are not available.</b>
                            </div>
                            <div>
                                <b>Reason:</b> <i>{errorMessage}</i>
                            </div>
                        </div>);
                }
                let params = getParameters();
                let channelsDB = false;
                if (params !== null) {
                    channelsDB = params.isChannelsDB;
                }
                controls.push(<button key={'reload'} className="reload-data btn btn-primary" onClick={() => this.reload(channelsDB, true)}>Reload Data</button>);
            }

            return <div className="d-flex flex-column h-100">
                <div className="tab-content">
                    {controls}
                </div>
                <div id="left-panel-tabs" className="left-panel-tabs">
                    <ul className="nav nav-tabs flex-column" role="tablist">
                        <li key={'home'} className="nav-item" title="Home"><a id="home-tab" className="nav-link left-panel-tab" href="/" role="tab"><i className="bi bi-house fs-5"></i></a></li>
                        <li key={'channels'} className="nav-item"><a style={{ writingMode: "vertical-lr" }} className="nav-link active left-panel-tab disabled" id="ui-tab" data-bs-toggle="tab" data-bs-target="#ui" role="tab" aria-controls="ui" aria-selected="true" aria-disabled="true">Channels</a></li>
                        <li key={'compute'} className="nav-item"><a style={{ writingMode: "vertical-lr" }} className="nav-link left-panel-tab disabled" id="controls-tab" data-bs-toggle="tab" data-bs-target="#controls" role="tab" aria-controls="controls" aria-selected="true" aria-disabled="true">Compute</a></li>
                    </ul>
                </div>
            </div>;
        }
    }
}