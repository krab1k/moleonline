import React from "react";
import { Events as BridgeEvents, CopyParametersParams, Events } from "../../../Bridge";
import { Events as FormEvents } from "../../CommonUtils/FormEvents";
import { ApiService, formatChains, isDone, Job, JobState, MoleConfig, NewJob, PoresConfig, Session } from "../../../MoleAPIService";
import { describeError, RequestError } from "../../../Http";
import { MoleFormData, PoresFormData } from "./FormData";
import { JobStatus, SessionInfo } from "../../../DataProxy";
import { TooltipText, WeightFunctions } from "../../../StaticData";
import { flattenResidues, flattenResiduesArray, parseResidues, pointsToString } from "../../CommonUtils/Misc";
import { SimpleComboBox } from "../Common/Controls/Components";
import { fakeRedirect, getParameters } from "../../../Common/Util/Router";
import { TabbedContainer } from "../../../Common/UI/Tabs/BootstrapTabs";
import { CommonOptions } from "../../../../config/common";
import { ControlGroup } from "molstar/lib/mol-plugin-ui/controls/common";
import { BoolControl, NumberRangeControl, SelectControl, TextControl } from "molstar/lib/mol-plugin-ui/controls/parameters";
import { ParamDefinition } from "molstar/lib/mol-util/param-definition";
import { StartingPointBox } from "./StartingPoints";
import { getParamsKind } from "./ParamsChecker";
import "molstar/lib/mol-plugin-ui/skin/light.scss";

const Provider = SessionInfo.DataProvider;

let validationGroup = "SettingsFormValidatonGroup";

export class Controls extends React.Component {

    private unsubscribe: (() => void)[] = [];

    componentDidMount() {
        this.unsubscribe.push(BridgeEvents.subscribeNewSubmit((submitId) => {
            this.forceUpdate();
        }));
    }

    componentWillUnmount() {
        this.unsubscribe.forEach(u => u());
        this.unsubscribe = [];
    }

    render() {
        return (
            <ControlTabs />
        );
    }
}

interface SettingsProps {
    initialData: Session,
    submitId: number,
    parent: ControlTabs,
}

interface SettingsState {
    computationId: string,
    moleFormData: MoleFormData,
    poresFormData: PoresFormData,
    mode: "Channels" | "Pores",
};
export class Settings extends React.Component<SettingsProps, SettingsState> {
    private unsubscribe: (() => void)[] = [];

    state: SettingsState = {
        moleFormData: this.getMoleDefaultValues(),
        poresFormData: this.getPoresDefaultValues(),
        computationId: this.props.initialData.id,
        mode: "Channels",
    }

    getMoleDefaultValues() {
        let data = new MoleFormData();

        data.setIgnoreHETATMs(true);
        data.setIgnoreHydrogens(false);
        data.setProbeRadius(5);
        data.setInteriorThreshold(1.1);
        data.setOriginRadius(5);
        data.setSurfaceCoverRadius(10);
        data.setWeightFunction("VoronoiScale");
        data.setMergePores(false);
        data.setAutomaticPores(false);
        data.setBottleneckRadius(1.2);
        data.setBottleneckTolerance(3);
        data.setMaxTunnelSimilarity(0.7);

        return data;
    }

    getPoresDefaultValues() {
        let data = new PoresFormData();

        data.setBetaStructure(false);
        data.setMembraneRegion(false);
        data.setProbeRadius(13);
        data.setInteriorThreshold(0.8);

        return data;
    }

    handleSubmitPromise(promise: Promise<Job>) {
        const sessionId = this.state.computationId;
        promise
            .then((job) => {
                fakeRedirect(sessionId, String(job.number));

                Provider.get(sessionId, ((_id: string, session: Session) => {
                    JobStatus.Watcher.registerOnChangeHandler(sessionId, job.number, (current) => {
                        if (isDone(current.state)) {
                            this.props.parent.setState({ canSubmit: true });
                            BridgeEvents.invokeSubmitDone(job.number, current.state);
                            if (current.state === "failed") {
                                BridgeEvents.invokeNotifyMessage({
                                    messageType: "Danger",
                                    message: `Submission #${job.number} failed: ${current.error}`
                                });
                            }
                        }
                    }, (err) => {
                        BridgeEvents.invokeNotifyMessage({
                            messageType: "Danger",
                            message: describeError(err, "Job status cannot be tracked. Please try to refresh the page.")
                        })
                    })

                    this.props.parent.setState({ data: session });

                    BridgeEvents.invokeNewSubmit(job.number);
                    BridgeEvents.invokeChangeSubmitId(job.number);

                }).bind(this), true);

                BridgeEvents.invokeToggleLoadingScreen({
                    message: "Submited job in progress...",
                    visible: true
                });
            })
            .catch((err: any) => {
                this.props.parent.setState({ canSubmit: true });

                if (CommonOptions.DEBUG_MODE)
                    console.log(err);
                BridgeEvents.invokeNotifyMessage({
                    // 409 (still running or loading) and 503 (server busy): try again later
                    messageType: err instanceof RequestError && (err.status === 409 || err.status === 503) ? "Warning" : "Danger",
                    message: describeError(err, "Job submit was not completed succesfully! Please try again later.")
                })
            })
    }

    componentDidMount() {
        this.unsubscribe.push(FormEvents.attachOnSubmitEventHandler((formGroup) => {
            if (formGroup !== validationGroup) {
                return;
            }
            const job: NewJob = this.state.mode === "Channels"
                ? { type: "tunnels", parameters: this.state.moleFormData.getPackage() }
                : { type: "pores", parameters: this.state.poresFormData.getPackage() };
            this.handleSubmitPromise(ApiService.createJob(this.state.computationId, job));
        }));

        this.unsubscribe.push(BridgeEvents.subscribeOnReSubmit((promise) => {
            this.handleSubmitPromise(promise);
        }));

        this.unsubscribe.push(FormEvents.attachOnClearEventHandler((formGroup) => {
            if (formGroup !== validationGroup + "_form") {
                return;
            }
            this.props.parent.setState({ canSubmit: true });
            const mode = this.state.mode;
            this.setState({
                mode: mode === "Channels" ? "Pores" : "Channels", //setting mode to oposite to trigger update
                moleFormData: this.getMoleDefaultValues(),
                poresFormData: this.getPoresDefaultValues(),
            }, () => this.setState({ mode })); //setting back correct mode
        }));

        this.unsubscribe.push(BridgeEvents.subscribeCopyParameters((params: CopyParametersParams) => {
            let s1 = this.props.parent.state;
            this.props.parent.setState({
                activeTabIdx: 0,
                submitId: s1.submitId,
                canSubmit: s1.canSubmit,
                data: s1.data,
                err: s1.err
            }, () => {
                this.setState({
                    mode: (params.mode === "mole") ? "Pores" : "Channels", //Change to oposite mode to final desired mode to trigger subcomponent re-render
                }, () => {
                    this.setState({
                        moleFormData: (params.mode === "mole" && params.moleConfig !== null) ? new MoleFormData(params.moleConfig) : this.getMoleDefaultValues(),
                        poresFormData: (params.mode === "pores" && params.poresConfig !== null) ? new PoresFormData(params.poresConfig) : this.getPoresDefaultValues(),
                        mode: (params.mode === "mole") ? "Channels" : "Pores", //Change to correct and final mode to trigger subcomponent re-render
                    })
                });
            });
        }));
    }

    componentWillUnmount() {
        this.unsubscribe.forEach(u => u());
        this.unsubscribe = [];
    }

    render() {
        let form = <div />;

        if (this.state.mode === "Channels") {
            form = this.getMoleFormNew();
        }
        else if (this.state.mode === "Pores") {
            form = this.getPoresForm();
        }

        return (
            <div>
                <div className="mode-switch-button-container">
                    <span className="btn-sm btn-primary mode-switch" onClick={(e) => {
                        this.setState({ mode: this.state.mode === "Channels" ? "Pores" : "Channels" });
                    }}>Switch to {(this.state.mode === "Channels") ? "Pore" : "Channels"} mode</span>
                </div>
                {form}
            </div>
        );
    }

    // onChange of a form control: sets its value in the form data and renders the form again
    private mole(set: (data: MoleFormData, value: any) => void) {
        return (v: { value: any }) => {
            if (this.state.moleFormData !== null) {
                set(this.state.moleFormData, v.value);
                this.forceUpdate();
            }
        };
    }

    private pores(set: (data: PoresFormData, value: any) => void) {
        return (v: { value: any }) => {
            if (this.state.poresFormData !== null) {
                set(this.state.poresFormData, v.value);
                this.forceUpdate();
            }
        };
    }

    getMoleFormNew() {
        if (this.state.moleFormData === null) {
            return <div />
        }

        let data = this.state.moleFormData;
        return <div className="settings-form basic-settings">
            <h3>Channels</h3>
            <ControlGroup header='Active Atoms/Residues' initialExpanded={this.props.parent.state.openControls}>
                <BoolControl name='Ignore HETATMs' param={ParamDefinition.Boolean(true, { label: 'Ignore HETATMs' })} value={valueOrDefault(data.getIgnoreHETATMs(), true)} onChange={this.mole((d, v) => d.setIgnoreHETATMs(v))}
                />
                <ControlGroup header='Advanced options' initialExpanded={this.props.parent.state.openControls}>
                    <BoolControl name='Ignore Hydrogens' param={ParamDefinition.Boolean(false, { label: 'Ignore Hydrogens' })} value={valueOrDefault(data.getIgnoreHydrogens(), false)} onChange={this.mole((d, v) => d.setIgnoreHydrogens(v))}
                    />
                    <TextControl name="Query Filter" param={ParamDefinition.Text('', { label: 'Query Filter', placeholder: 'Residues(\'GOL\')' })} value={valueOrDefault(data.getQueryFilter(), "")} onChange={this.mole((d, v) => d.setQueryFilter(v))} />
                    <TextControl name="Ignored Residues" param={ParamDefinition.Text('', { label: 'Ignored Residues', placeholder: 'A 69, A 386, ...' })} value={valueOrDefault(data.getIgnoredResiduesStr(), "")} onChange={this.mole((d, v) => { d.setIgnoredResidues(parseResidues(v)); d.setIgnoredResiduesStr(v); })} />
                    <TextControl name="Specific Chains" param={ParamDefinition.Text('', { label: 'Specific Chains', placeholder: 'A, B, ...' })} value={valueOrDefault(data.getSpecificChains(), "")} onChange={this.mole((d, v) => d.setSpecificChains(v))} />
                </ControlGroup>
            </ControlGroup>
            <ControlGroup header='Cavity Parameters' initialExpanded={this.props.parent.state.openControls}>
                <NumberRangeControl name="Probe Radius" param={ParamDefinition.Numeric(5, { min: 1.4, max: 45, step: 0.01 })} value={valueOrDefault(data.getProbeRadius(), 5)} onChange={this.mole((d, v) => d.setProbeRadius(v))} />
                <NumberRangeControl name="Interior Treshold" param={ParamDefinition.Numeric(1.1, { min: 0.3, max: 3, step: 0.01 })} value={valueOrDefault(data.getInteriorThreshold(), 1.1)} onChange={this.mole((d, v) => d.setInteriorThreshold(v))} />
            </ControlGroup>
            <ControlGroup header='Channel Parameters' initialExpanded={this.props.parent.state.openControls}>
                <NumberRangeControl name="Origin Radius" param={ParamDefinition.Numeric(5, { min: 0.1, max: 10, step: 0.05 })} value={valueOrDefault(data.getOriginRadius(), 5)} onChange={this.mole((d, v) => d.setOriginRadius(v))} />
                <NumberRangeControl name="Surface Cover Radius" param={ParamDefinition.Numeric(10, { min: 5, max: 20, step: 0.5 })} value={valueOrDefault(data.getSurfaceCoverRadius(), 10)} onChange={this.mole((d, v) => d.setSurfaceCoverRadius(v))} />
                <SelectControl
                    name="Weight Function"
                    param={ParamDefinition.Select(valueOrDefault(data.getWeightFunction(), "VoronoiScale"), ParamDefinition.arrayToOptions(WeightFunctions.getFunctions()))}
                    value={valueOrDefault(data.getWeightFunction(), "VoronoiScale")}
                    onChange={this.mole((d, v) => d.setWeightFunction(v))} />
                <BoolControl name='Merge Pores' param={ParamDefinition.Boolean(false, { label: 'Merge Pores' })} value={valueOrDefault(data.getMergePores(), false)} onChange={this.mole((d, v) => d.setMergePores(v))}
                />
                <BoolControl name='Automatic Pores' param={ParamDefinition.Boolean(false, { label: 'Automatic Pores' })} value={valueOrDefault(data.getAutomaticPores(), false)} onChange={this.mole((d, v) => d.setAutomaticPores(v))}
                />
                <ControlGroup header='Advanced options'>
                    <NumberRangeControl name="Bottleneck Radius" param={ParamDefinition.Numeric(1.2, { min: 0, max: 5, step: 0.01 })} value={valueOrDefault(data.getBottleneckRadius(), 1.2)} onChange={this.mole((d, v) => d.setBottleneckRadius(v))} />
                    <NumberRangeControl name="Bottleneck Tolerance" param={ParamDefinition.Numeric(3, { min: 0, max: 5, step: 0.1 })} value={valueOrDefault(data.getBottleneckTolerance(), 3)} onChange={this.mole((d, v) => d.setBottleneckTolerance(v))} />
                    <NumberRangeControl name="Max Tunnel Similarity" param={ParamDefinition.Numeric(0.7, { min: 0, max: 1, step: 0.05 })} value={valueOrDefault(data.getMaxTunnelSimilarity(), 0.7)} onChange={this.mole((d, v) => d.setMaxTunnelSimilarity(v))} />
                </ControlGroup>
            </ControlGroup>
            <ControlGroup header='Selection' initialExpanded={this.props.parent.state.openControls}>
                <StartingPointBox label="Starting Point" tooltip={TooltipText.get("startingPoint")} defaultItems={this.state.moleFormData.getStartingPoints()} noDataText={"No starting points selected..."} onChange={v => this.state.moleFormData?.setStartingPoints(v)} allowPatternQuery={true} />
                <StartingPointBox label="End Point" tooltip={TooltipText.get("endPoint")} defaultItems={this.state.moleFormData.getEndingPoints()} noDataText={"No end points selected..."} onChange={v => this.state.moleFormData?.setEndPoints(v)} allowPatternQuery={false} />
            </ControlGroup>
        </div>
    }

    getPoresForm() {
        if (this.state.poresFormData === null) {
            return <div />
        }

        let data = this.state.poresFormData;

        return <div className="settings-form basic-settings pores">
            <h3>Pores</h3>
            <BoolControl name='Beta Structure' param={ParamDefinition.Boolean(true, { label: 'Beta Structure' })} value={data.getBetaStructure()} onChange={this.pores((d, v) => d.setBetaStructure(v))}
            />
            <BoolControl name='Membrane Region' param={ParamDefinition.Boolean(true, { label: 'Membrane Region' })} value={data.getMembraneRegion()} onChange={this.pores((d, v) => d.setMembraneRegion(v))}
            />
            <TextControl name="Specific Chains" param={ParamDefinition.Text('', { label: 'Specific Chains', placeholder: 'A, B, ...' })} value={data.getSpecificChains()} onChange={this.pores((d, v) => d.setSpecificChains(v))} />
            <NumberRangeControl name="Probe Radius" param={ParamDefinition.Numeric(13, { min: 1.4, max: 45, step: 0.01 })} value={valueOrDefault(data.getProbeRadius(), 13)} onChange={this.pores((d, v) => d.setProbeRadius(v))} />
            <NumberRangeControl name="Interior Treshold" param={ParamDefinition.Numeric(0.8, { min: 0.3, max: 3, step: 0.01 })} value={valueOrDefault(data.getInteriorThreshold(), 0.8)} onChange={this.pores((d, v) => d.setInteriorThreshold(v))} />
        </div>
    }
}

function valueOrDefault(value: any | null, def: any) {
    return (value === null) ? def : value;
}

function getSubmissionIdx(session: Session, number: number): number | null {
    const idx = session.jobs.findIndex(j => j.number === number);
    return idx === -1 ? null : idx;
}

function deepEqual(objA: any, objB: any): boolean {
    if (objA === objB) return true; // Same reference or both null/undefined

    if (typeof objA !== "object" || typeof objB !== "object" || objA === null || objB === null) {
        return false; // Primitives or one of them is null
    }

    const keysA = Object.keys(objA);
    const keysB = Object.keys(objB);

    if (keysA.length !== keysB.length) {
        return false;
    }

    return keysA.every(key => deepEqual(objA[key], objB[key]));
}

interface SubmissionsProps {
    computationInfo: Session,
    onResubmit: (info: Session) => void
};
interface SubmissionsState {
    computationInfo: Session | null,
    loading: boolean,
};
export class Submissions extends React.Component<SubmissionsProps, SubmissionsState> {

    state: SubmissionsState = { computationInfo: null, loading: true }
    private hasKillable = false;

    componentDidUpdate(prevProps: { computationInfo: Session }) {
        if (
            this.state.computationInfo !== null &&
            deepEqual(prevProps.computationInfo, this.props.computationInfo)
        ) {
            return;
        }

        this.prepareSubmissionData(this.props.computationInfo);
    }

    private prepareSubmissionData(computationInfo: Session) {
        this.setState({ computationInfo: computationInfo });

        let hasKillable = false;

        for (let job of computationInfo.jobs) {
            if (isDone(job.state)) {
                // finished, failed or cancelled: its state stays
                continue;
            }
            hasKillable = true;
            JobStatus.Watcher.registerOnChangeHandler(this.props.computationInfo.id, job.number, (current) => {
                if (current.state !== job.state) {
                    let session = this.state.computationInfo;
                    if (session === null) {
                        console.error(`Session was not initialized corectly.`);
                        return;
                    }
                    let subIdx = getSubmissionIdx(session, job.number);
                    if (subIdx === null) {
                        console.error(`Submission #${job.number} not found.`);
                        return;
                    }
                    session.jobs[subIdx] = current;
                    this.setState({ computationInfo: session });
                    let hasKillable_ = this.checkHasKillable(session);
                    if (this.hasKillable !== hasKillable_) {
                        this.hasKillable = hasKillable_;
                        BridgeEvents.invokeChangeHasKillable(hasKillable_);
                    }
                    // done (e.g. it was running when the page opened): its channels are loaded like those of a new one
                    if (isDone(current.state)) {
                        BridgeEvents.invokeSubmitDone(job.number, current.state);
                    }
                }
            }, (err) => {
                if (CommonOptions.DEBUG_MODE)
                    console.log(err);
            }
            );
        }
        this.hasKillable = hasKillable;

        this.setState({ loading: false });

        if (hasKillable) {
            BridgeEvents.invokeChangeHasKillable(hasKillable);
        }
    }

    // a queued or running job can be cancelled
    private checkHasKillable(session: Session) {
        return session.jobs.some(j => !isDone(j.state));
    }

    componentDidMount() {
        this.prepareSubmissionData(this.props.computationInfo);
    }

    render() {
        if (this.state.computationInfo !== null && !this.state.loading) {
            let submissions: React.JSX.Element[] = [];
            let submissionsData = this.state.computationInfo.jobs;
            let submitId = 1;
            let params = getParameters();
            if (params !== null) {
                submitId = (params.isChannelsDB) ? -1 : params.submitId;
            }

            for (let s of submissionsData.sort((a, b) => {
                return a.number - b.number;
            })) {
                submissions.push(
                    <Submission key={s.number} data={s} currentSubmitId={submitId} computationId={this.props.computationInfo.id} onResubmit={this.props.onResubmit} onCopy={(number: number) => {
                        const job = this.props.computationInfo.jobs.find(j => j.number === number);
                        if (job !== undefined) {
                            BridgeEvents.invokeCopyParameters({
                                mode: job.type === "tunnels" ? "mole" : "pores",
                                moleConfig: job.type === "tunnels" ? job.parameters : null,
                                poresConfig: job.type === "pores" ? job.parameters : null
                            });
                        }
                    }} />
                );
            }

            if (submissions.length === 0) {
                return (
                    <div className="panel panel-default">
                        <div className="panel-heading">
                            <h4 className="panel-title">
                                No submissions found.
                            </h4>
                        </div>
                    </div>
                );
            }

            return (
                <div className="panel-group submissions">
                    {submissions}
                </div>
            );
        }
        else if (this.state.loading) {
            return (
                <div className="panel panel-default">
                    <div className="panel-heading">
                        <h4 className="panel-title">
                            Submissions data loading...
                        </h4>
                    </div>
                </div>
            )
        }
        else {
            return (
                <div className="panel panel-default">
                    <div className="panel-heading">
                        <h4 className="panel-title">
                            No submissions data available.
                        </h4>
                    </div>
                </div>
            )
        }
    }
}

// The states as the submissions list shows them (the CSS class is state-<state>)
const STATE_LABELS: Record<JobState, string> = {
    queued: "Queued",
    running: "Running",
    finished: "Finished",
    failed: "Failed",
    cancelled: "Cancelled",
};

export class Submission extends React.Component<{ data: Job, computationId: string, currentSubmitId: number, onCopy: (number: number) => void, onResubmit: (info: Session) => void }, {}> {

    getMoleJob(params: MoleConfig) {
        return <div className="panel-body">
            <h4>Active Atoms/Residues</h4>
            Ignore Hydrogens: {(params.Cavity === void 0) ? "False" : (params.Cavity.IgnoreHydrogens) ? "True" : "False"}<br />
            Ignore HETATMs: {(params.Cavity === void 0) ? "False" : (params.Cavity.IgnoreHETAtoms) ? "True" : "False"}<br />
            Query Filter: {(params.QueryFilter === void 0) ? "" : params.QueryFilter}<br />
            Ignored Residues: {(params.NonActiveResidues === void 0 || params.NonActiveResidues === null) ? "" : flattenResidues(params.NonActiveResidues)}<br />
            Specific Chains: {(params.Input === void 0) ? "" : params.Input.SpecificChains}<br />
            <h4>Cavity Parameters</h4>
            Probe Radius: {(params.Cavity === void 0) ? "" : params.Cavity.ProbeRadius}<br />
            Interior Threshold: {(params.Cavity === void 0) ? "" : params.Cavity.InteriorThreshold}<br />
            <h4>Channel Parameters</h4>
            Origin Radius: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.OriginRadius}<br />
            Surface Cover Radius: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.SurfaceCoverRadius}<br />
            Weight Function: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.WeightFunction}<br />
            Bottleneck Radius: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.BottleneckRadius}<br />
            Bottleneck Tolerance: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.BottleneckTolerance}<br />
            Max Tunnel Similarity: {(params.Tunnel === void 0 || params.Tunnel === null) ? "" : params.Tunnel.MaxTunnelSimilarity}<br />
            Merge Pores: {(params.PoresMerged === void 0 || params.PoresMerged === null) ? "False" : (params.PoresMerged) ? "True" : "False"}<br />
            Automatic Pores: {(params.PoresAuto === void 0 || params.PoresAuto === null) ? "False" : (params.PoresAuto) ? "True" : "False"}<br />
            <h4>Selection</h4>
            Starting Point: {(params.Origin === void 0 || params.Origin === null) ? "" : (params.Origin.Residues === void 0 || params.Origin.Residues === null || params.Origin.Residues.length === 0) ? "" : flattenResiduesArray(params.Origin.Residues)}<br />
            Starting Point[x,y,z]: {(params.Origin === void 0 || params.Origin === null) ? "" : (params.Origin.Points === void 0 || params.Origin.Points === null) ? "" : pointsToString(params.Origin.Points)}<br />
            End Point: {(params.CustomExits === void 0 || params.CustomExits === null) ? "" : (params.CustomExits.Residues === void 0 || params.CustomExits.Residues === null || params.CustomExits.Residues.length === 0) ? "" : flattenResiduesArray(params.CustomExits.Residues)}<br />
            End Point[x,y,z]: {(params.CustomExits === void 0 || params.CustomExits === null) ? "" : (params.CustomExits.Points === void 0 || params.CustomExits.Points === null) ? "" : pointsToString(params.CustomExits.Points)}<br />
            Query: {(params.Origin === void 0 || params.Origin === null) ? "" : (params.Origin.QueryExpression === void 0 || params.Origin.QueryExpression === null) ? "" : params.Origin.QueryExpression}<br />
        </div>
    }

    getPoresJob(params: PoresConfig) {
        return <div className="panel-body">
            Beta Structure: {(params.IsBetaBarrel) ? "True" : "False"}<br />
            Membrane Region: {(params.InMembrane === void 0) ? "False" : (params.InMembrane) ? "True" : "False"}<br />
            Specific Chains: {formatChains(params.Chains)}<br />
            Probe Radius: {(params === void 0) ? "" : params.ProbeRadius}<br />
            Interior Threshold: {(params === void 0) ? "" : params.InteriorThreshold}<br />
        </div>
    }

    render() {
        let currentSubmitId = this.props.currentSubmitId;
        let data = this.props.data;
        let canResubmit = isDone(data.state);
        let contents = data.type === "tunnels" ? this.getMoleJob(data.parameters) : this.getPoresJob(data.parameters);

        return (
            <div className="panel panel-default">
                <div className="panel-heading">
                    <a data-bs-toggle="collapse" href={`#submit-data-${data.number}`} onClick={(e) => {
                        if (e.currentTarget.attributes.getNamedItem('aria-expanded')!.value === 'true') {
                            if (data.number !== this.props.currentSubmitId) {
                                changeSubmitId(this.props.computationId, data.number);
                            }
                        }
                    }}>
                        <h4 className="panel-title">
                            #{data.number}
                        </h4>
                        <div className="submission-state">
                            Status: <span className={`state-${data.state}`}>{STATE_LABELS[data.state]}</span>
                        </div>
                        {data.error ? <div className="submission-error">{data.error}</div> : null}
                    </a>
                </div>
                <div id={`submit-data-${data.number}`} className={`panel-collapse collapse${(currentSubmitId === data.number) ? ' show' : ''}`}>
                    {contents}
                    <div className="panel-footer">
                        <span className="btn btn-sm btn-primary" onClick={(() => this.exportParams()).bind(this)}>Export</span><span className="btn btn-sm btn-primary" onClick={(() => this.copyParams(data.number)).bind(this)}>Copy</span><span className="btn btn-sm btn-primary" aria-disabled={!canResubmit} onClick={(() => this.reSubmit()).bind(this)}>Resubmit</span>
                    </div>
                </div>
            </div>
        );
    }

    private reSubmit() {
        const data = this.props.data;
        const job: NewJob = data.type === "tunnels" ? { type: "tunnels", parameters: data.parameters } : { type: "pores", parameters: data.parameters };
        BridgeEvents.invokeOnReSubmit(ApiService.createJob(this.props.computationId, job));
    }

    private copyParams(submitId: number) {
        if (this.props.onCopy !== void 0) {
            this.props.onCopy(submitId);
        }
    }

    private exportParams() {
        if (this.props === undefined) {
            return;
        }
        let currentSubmitId = this.props.currentSubmitId;
        let computationId = this.props.computationId;
        let data = this.props.data;
        const result = data.type === "tunnels" ? new MoleFormData(data.parameters).getPackage() : new PoresFormData(data.parameters).getPackage();

        const jsonString = JSON.stringify(result, null, 2);

        const blob = new Blob([jsonString], { type: "application/json" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `params_${computationId}_${currentSubmitId}.json`;

        link.click();

        URL.revokeObjectURL(link.href);
    }
}

function changeSubmitId(computationId: string, submitId: number) {
    if (submitId === -2) {
        fakeRedirect(computationId, "FromFile");
    } else if (submitId === -1) {
        fakeRedirect(computationId, "ChannelsDB");
    } else if (submitId === 0) {
        fakeRedirect(computationId, "")
    }
    else {
        fakeRedirect(computationId, (submitId > 0) ? String(submitId) : void 0);
    }
    BridgeEvents.invokeChangeSubmitId(submitId);
}

interface ControlTabState {
    activeTabIdx: number,
    data?: Session,
    err?: String,
    submitId: number,
    canSubmit: boolean,
    openControls: boolean,
};
export class ControlTabs extends React.Component<{ activeTab?: number }, ControlTabState> {

    state: ControlTabState = {
        activeTabIdx: 0,
        data: void 0,
        err: void 0,
        submitId: 1,
        canSubmit: true,
        openControls: false
    }

    private unsubscribe: (() => void)[] = [];

    componentDidMount() {
        if (this.props.activeTab !== void 0) {
            this.setState({ activeTabIdx: this.props.activeTab });
        }

        let parameters = getParameters();
        if (parameters !== null) {
            let submitId = parameters.submitId;
            if (parameters.isChannelsDB) {
                submitId = -1;
            }
            Provider.get(parameters.computationId, ((_id: string, session: Session) => {
                this.setState({ data: session, submitId: submitId });
            }).bind(this));
        } else {
            this.setState({ err: "Parameters from url cannot be properly processed." });
        }

        this.unsubscribe.push(BridgeEvents.subscribeChangeSubmitId((submitId) => {
            this.setState({ submitId: submitId, openControls: false });
        }));
    }

    componentWillUnmount() {
        this.unsubscribe.forEach(u => u());
        this.unsubscribe = [];
    }

    handleSubmit(e: React.FormEvent<HTMLFormElement>) {
        e.preventDefault();

        this.setState({ canSubmit: false });

        if (this.state.data === void 0) {
            return;
        }

        FormEvents.invokeOnSubmit(validationGroup);
    }

    setOpenControls(open: boolean) {
        this.setState({ openControls: open });
    }

    render() {
        let tabs: React.JSX.Element[] = [];

        if (this.state.data !== void 0) {
            tabs.push(
                <Settings initialData={this.state.data} parent={this} submitId={this.state.submitId} />
            );
            tabs.push(
                <Submissions computationInfo={this.state.data} onResubmit={(info) => {
                    this.setState({ data: info });
                }} />
            );
        }
        else {
            tabs.push(
                <div>No data</div>
            );
        }
        return (
            <div className="submit-form-container">
                <TabbedContainer header={["Submission settings", "Submissions"]} tabContents={tabs} namespace="right-panel-tabs-" htmlClassName="tabs flex-grow-1" htmlId="right-panel-tabs" activeTab={this.state.activeTabIdx} onChange={((tabIdx: number) => {
                    this.setState({ activeTabIdx: tabIdx });
                }).bind(this)} />
                <form className="form-horizontal" id="submission-form" onSubmit={this.handleSubmit.bind(this)}>
                    <ControlButtons submitId={this.state.submitId} computationInfo={this.state.data} isLoading={!this.state.canSubmit} setOpenControls={this.setOpenControls.bind(this)} />
                </form>
            </div>
        );
    }
}

interface SubmissionCoboboxItem {
    label: string,
    value: string
}
interface ControlButtonsState {
    submitId: number,
    hasKillable: boolean,
    submitLoading: boolean,
}
interface ControlButtonsProps {
    submitId: number,
    computationInfo: Session | undefined,
    isLoading: boolean,
    setOpenControls: (open: boolean) => void,
}

export class ControlButtons extends React.Component<ControlButtonsProps, ControlButtonsState> {
    state: ControlButtonsState = { submitId: this.props.submitId, hasKillable: false, submitLoading: false }
    private unsubscribe: (() => void)[] = [];

    componentDidMount() {
        this.unsubscribe.push(BridgeEvents.subscribeChangeHasKillable((hasKillable) => {
            this.setState({ hasKillable: hasKillable });
        }));
        this.unsubscribe.push(BridgeEvents.subscribeToggleLoadingScreen(({ message, visible }) => {
            this.setState({ submitLoading: visible });
        }));
    }

    componentWillUnmount() {
        this.unsubscribe.forEach(u => u());
        this.unsubscribe = [];
    }

    componentDidUpdate(prevProps: ControlButtonsProps) {
        if (prevProps.submitId !== this.props.submitId) {
            this.setState({ submitId: this.props.submitId });
        }
    }

    private getSubmissions(): Job[] {
        return [...this.props.computationInfo?.jobs ?? []].sort((a, b) => a.number - b.number);
    }

    private prepareSubmissionItems(): SubmissionCoboboxItem[] {
        let submissions = this.getSubmissions();

        let rv = [];

        if (this.props.computationInfo !== void 0) {
            if (this.props.computationInfo.pdb_id !== null) {
                rv.push({
                    label: 'ChDB',
                    value: '-1'
                });
            } else {
                rv.push({
                    label: 'File',
                    value: '-2'
                });
            }
        }

        rv.push(
            {
                label: `-`,
                value: '0'
            }
        );

        if (submissions.length === 0) {
            return rv;
        }

        for (let item of submissions) {
            rv.push(
                {
                    label: `${item.number}`,
                    value: `${item.number}`
                }
            );
        }

        return rv;
    }

    private getSelectedIndex(submitId: number, items: SubmissionCoboboxItem[]): number | undefined {
        const idx = items.findIndex(item => item.value === `${submitId}`);
        return idx === -1 ? void 0 : idx;
    }

    private onSubmitIdComboSelectChange(e: any) {
        if (this.props.computationInfo === void 0) {
            return;
        }

        let idx = e.currentTarget.selectedIndex;
        let submitId = (e.currentTarget.options[idx] as HTMLOptionElement).value;
        let sid = Number(submitId).valueOf();
        changeSubmitId(this.props.computationInfo.id, sid);
        this.setState({ submitId: sid });
    }

    private changeSubmitIdByStep(e: React.MouseEvent<HTMLInputElement>) {
        if (this.props.computationInfo === void 0) {
            return;
        }

        let submitId = e.currentTarget.dataset["value"];

        if (submitId !== void 0) {
            let sid = Number(submitId).valueOf();
            changeSubmitId(this.props.computationInfo.id, sid);
            this.setState({ submitId: sid });
        }
    }

    private canShift(left: boolean) {
        if (this.props.computationInfo === void 0) {
            return false;
        }

        let submissionsItems = this.prepareSubmissionItems();
        let submissions = this.getSubmissions();

        if (String(this.state.submitId) === String(0)) {
            if (!left) return submissions.length > 0;
            return submissionsItems.length > 0 && submissionsItems[0].value !== "0";
        }

        if (String(this.state.submitId) === String(-1)) {
            return !left;
        }

        if (String(this.state.submitId) === String(-2)) {
            return !left;
        }

        for (let idx = 0; idx < submissions.length; idx++) {
            if (submissions[idx].number === this.props.submitId) {
                if (!left) {
                    let nextIdx = idx + 1;
                    return nextIdx < submissions.length;
                }
                return true;
            }
        }

        return false;
    }

    private canShiftNext() {
        return this.canShift(false);
    }

    private canShiftPrev() {
        return this.canShift(true);
    }

    private getNextIdx(idx: number): number {
        return idx + 1;
    }

    private getPrevIdx(idx: number): number {
        return idx - 1;
    }

    private handleOnReaderLoad(reader: FileReader, props: Readonly<ControlButtonsProps>) {
        if (props === undefined || props.setOpenControls === undefined) {
            return;
        }
        try {
            const jsonData = JSON.parse(reader.result as string);
            const check = getParamsKind(jsonData);
            BridgeEvents.invokeCopyParameters({
                mode: check === "MoleConfig" ? "mole" : "pores",
                moleConfig: check === "MoleConfig" ? jsonData as MoleConfig : null,
                poresConfig: check === "PoresConfig" ? jsonData as PoresConfig : null
            });
            props.setOpenControls(true);
        } catch (error) {
            Events.invokeNotifyMessage({
                messageType: "Danger",
                message: `Error loading JSON: ${error instanceof Error ? error.message : error}`
            });
        }
    }

    private handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
        if (this.props === undefined) {
            return;
        }
        const file = event.target.files?.[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = () => this.handleOnReaderLoad(reader, this.props);
            reader.readAsText(file);
            event.target.value = '';
        }
    }

    render() {
        let canKill = (this.props.computationInfo !== void 0 && this.state.hasKillable);
        let items = this.prepareSubmissionItems();
        let idx = this.getSelectedIndex(this.state.submitId, items);
        let canShiftPrev = this.canShiftPrev();
        let canShiftNext = this.canShiftNext();
        return <div className="submit-parent">
            <div className="d-flex flex-wrap mb-1">
                {this.props.isLoading || this.state.submitLoading
                    ? <button className="btn btn-sm btn-success submit px-3" type="submit" id="controls-submit" disabled>
                        <span
                            id="controls-loader"
                            className="spinner-border spinner-border-sm me-2"
                            role="status"
                            aria-hidden="true"
                        ></span>
                        Submit
                    </button>
                    : <button className="btn btn-sm btn-success submit px-3" type="submit" id="controls-submit">Submit</button>}
                <input type="button" className="btn btn-sm btn-primary kill-job-button px-3" disabled={!canKill} onClick={() => this.killJob()} value="Kill" />
                <input type="button" className="btn btn-sm btn-primary delete-project-button px-3" onClick={() => this.deleteProject()} value="Delete" />
                <input className="btn btn-sm btn-primary clear-button px-3" type="button" value="Clear" onClick={() => {
                    FormEvents.invokeOnClear(validationGroup + "_form");
                }} />
            </div>
            <div className="d-flex flex-wrap">
                <label className="btn btn-sm btn-primary load-button px-2">
                    Load params
                    <input
                        type="file"
                        accept=".json"
                        className="d-none"
                        onChange={this.handleFileChange.bind(this)}
                    />
                </label>
                <div style={{ marginLeft: "5px" }}>
                    <input className="btn btn-sm btn-primary submit-arrow" type="button" value=">" disabled={(!canShiftNext) ? true : void 0} data-value={(!canShiftNext || idx === void 0) ? void 0 : items[this.getNextIdx(idx)].value} onClick={this.changeSubmitIdByStep.bind(this)} />
                    <SimpleComboBox id="submissionComboSwitch" items={items} defaultSelectedIndex={idx} className="form-control submit-combo" onSelectedChange={this.onSubmitIdComboSelectChange.bind(this)} />
                    <input className="btn btn-sm btn-primary submit-arrow" type="button" value="<" disabled={(!canShiftPrev) ? true : void 0} data-value={(!canShiftPrev || idx == void 0) ? void 0 : items[this.getPrevIdx(idx)].value} onClick={this.changeSubmitIdByStep.bind(this)} />
                </div>
            </div>
        </div>
    }

    private killJob() {
        const info = this.props.computationInfo;
        if (info === void 0 || !window.confirm("Do you really want to kill the running job?")) {
            return;
        }
        // the job still queued or running: one at a time on a session, the last one
        ApiService.getSession(info.id).then(session => {
            const running = session.jobs.filter(j => !isDone(j.state)).pop();
            if (running === undefined) {
                throw new Error("No job is running.");
            }
            return ApiService.cancelJob(info.id, running.number);
        }).then(() => {
            BridgeEvents.invokeNotifyMessage({
                message: "Job has been successfully killed.",
                messageType: "Success"
            });
        })
            .catch((err) => {
                BridgeEvents.invokeNotifyMessage({
                    message: describeError(err, "Attempt to kill running job failed! Please try again later."),
                    messageType: "Danger"
                });
            });
    }

    private deleteProject() {
        const info = this.props.computationInfo;
        if (info === void 0 || !window.confirm("Do you really want to delete the whole computation, with all its submissions?")) {
            return;
        }
        ApiService.deleteSession(info.id).then(() => {
            BridgeEvents.invokeNotifyMessage({
                message: "Current computation was successfully deleted. You will be redirected to initial page.",
                messageType: "Success"
            });
            window.setTimeout(() => {
                location.replace("/");
            }, 5000);
        })
            .catch((err) => {
                BridgeEvents.invokeNotifyMessage({
                    message: describeError(err, "Attempt to delete current computation failed! Please try again later."),
                    messageType: "Danger"
                });
            });
    }
}
