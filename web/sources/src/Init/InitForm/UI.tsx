import React from "react";
import {ApiService, Session} from "../../MoleAPIService";
import { describeError } from "../../Http";
import {Events} from "../../Bridge";
import {TabbedContainer} from "../../Common/UI/Tabs/BootstrapTabs";
import {getNthSession, LAST_N_SESSIONS_N} from "../../Common/Util/LastNSessions";
import {SessionInfo, SessionStatus} from "../../DataProxy";
import AlertMessages from "../../Common/UI/AlertMessages/UI";

// The API refuses larger uploads
const MAX_UPLOAD_MB = 30;

interface State {
    useAsymmetricUnit: boolean,
    activeTabIdx: number,
    progress: string | null, // the submit button's label while the computation is being started
};

// The submit button's label while the structure is loaded
const PROGRESS: Record<Session["state"], string> = {
    queued: "Waiting in the queue...",
    loading: "Loading the structure...",
    ready: "Loaded!",
    failed: "Failed",
};

export class InitForm extends React.Component<{}, State> {
    state: State = {
        useAsymmetricUnit: false,
        activeTabIdx: 0,
        progress: null,
    };

    private handleFormSubmit(e: React.FormEvent<HTMLFormElement>) {
        e.preventDefault();

        this.setState({ progress: 'Submitting...' });

        // every tab's fields stay in the form; a selected file wins
        const field = (name: string) => (e.target as HTMLFormElement).elements.namedItem(name) as HTMLInputElement;
        const pdbid = field('pdbid').value;
        const assemblyId = field('assembly').value.trim(); // empty: the preferred assembly
        const asymmetricUnit = field('asymmetric-unit').checked;
        const file = field('file').files?.[0];

        if (file === void 0) {
            this.waitUntilLoaded(ApiService.createSession(pdbid, asymmetricUnit ? "asymmetric" : assemblyId === '' ? "preferred" : assemblyId));
        }
        else {
            this.uploadFile(file);
        }

        return false;
    }

    // The API refuses a structure over MAX_UPLOAD_MB, also when unpacked (413); checking first saves the upload
    private async uploadFile(file: File) {
        const limit = MAX_UPLOAD_MB * 1024 * 1024;
        if (file.size > limit) {
            this.showError(`The file is too large, at most ${MAX_UPLOAD_MB} MB can be uploaded, also when unpacked.`);
            return;
        }
        // a gzip file ends with its unpacked size (mod 2^32, little-endian); only a hint, the API checks the real size
        if (file.name.toLowerCase().endsWith(".gz") && file.size >= 4) {
            let unpacked;
            try {
                unpacked = new DataView(await file.slice(-4).arrayBuffer()).getUint32(0, true);
            } catch {
                // e.g. the file was changed or removed after it was selected
                this.showError("The file could not be read, please select it again.");
                return;
            }
            if (unpacked > limit) {
                this.showError(`The file unpacks to ${Math.ceil(unpacked / 1024 / 1024)} MB, at most ${MAX_UPLOAD_MB} MB can be computed.`);
                return;
            }
        }

        let data = new FormData();
        data.append("file", file)
        this.waitUntilLoaded(ApiService.uploadSession(data));
    }

    // Re-enables the form and tells the user why the computation did not start
    private showError(message: string) {
        this.setState({ progress: null });
        Events.invokeNotifyMessage({
            messageType: "Danger",
            message
        });
    }

    private showRequestError(reason: unknown) {
        this.showError(describeError(reason, "The MOLE API could not be reached, please try again later."));
    }

    // Follows the new session until its structure is loaded, then opens it
    private async waitUntilLoaded(created: Promise<Session>) {
        let session: Session;
        try {
            session = await SessionStatus.waitUntilLoaded((await created).id, s => this.setState({ progress: PROGRESS[s.state] }));
        } catch (reason) {
            this.showRequestError(reason);
            return;
        }
        if (session.state === "failed") {
            this.showError(`The structure could not be loaded: ${session.error}`);
            return;
        }
        Events.invokeNotifyMessage({
            messageType: "Success",
            message: "The structure was loaded. You will be redirected to the detail page."
        });
        location.replace(`/online/${session.id}`);
    }

    render() {
        const tabs = [this.formByPDBID(), this.formByFile(), this.formByLastNSessions()];
        // the last sessions are opened by a click, they have nothing to submit
        const buttons = this.state.activeTabIdx === 2
            ? <span />
            : <input type="submit" name="next" className="button" id="frm-jobSetup-setupForm-next"
                value={this.state.progress ?? 'Submit'} disabled={this.state.progress !== null} />;

        return (
            <>
                <div className="card-header bg-primary text-white">
                    Submit job
                </div>
                <div className="card-body">
                    <div id="alert-messages" className="index">
                        <AlertMessages />
                    </div>
                    <div className="InitForm">
                        <form onSubmit={this.handleFormSubmit.bind(this)}>
                            <div className="groupbox">
                                <TabbedContainer header={["PDB ID", "File", "Last session"]} tabContents={tabs} namespace="quick-start-panel-tabs-" htmlClassName="tabs" htmlId="quick-start-panel-tabs" activeTab={this.state.activeTabIdx}
                                    onChange={(tabIdx: number) => this.setState({ activeTabIdx: tabIdx })} />
                            </div>

                            <div className="buttons">
                                {buttons}
                            </div>
                        </form>
                    </div>
                </div>
            </>
        );
    }

    formByPDBID() {
        return (
            <table style={{width: "100%"}}>
                <tbody>
                <tr>
                    <td><label htmlFor="frm-jobSetup-setupForm-code">PDB ID</label>:</td>
                    <td>
                        <input type="text" name="pdbid" maxLength={4} size={15} className="text"
                               id="frm-jobSetup-setupForm-code" defaultValue="1tqn" data-bs-toggle="tooltip"
                               title="PDB ID code as can be found on www.pdb.org, for example 1tqn."/>
                    </td>
                </tr>

                <tr>
                    <td><label htmlFor="frm-jobSetup-setupForm-unit">Assembly ID (optional)</label>:</td>
                    <td><input disabled={this.state.useAsymmetricUnit} type="text" name="assembly" maxLength={3}
                               size={15} className="text" id="frm-jobSetup-setupForm-unit"
                               defaultValue="" placeholder="preferred" data-bs-toggle="tooltip"
                               title="Protein structures are resolved in asymmetric units divided or combined in PDB into assemblies with integer ID numbering.
                               Assemblies may or may not form biological units. Empty: the preferred assembly of the entry, usually its biological unit."/>
                    </td>
                </tr>
                <tr>
                    <td><label htmlFor="frm-jobSetup-setupForm-asymmetric-unit">Use asymmetric unit</label>:</td>
                    <td><input type="checkbox" onChange={e => this.setState({ useAsymmetricUnit: e.target.checked })}
                               id="frm-jobSetup-setupForm-asymmetric-unit" name="asymmetric-unit" className="checkbox"
                               defaultChecked={false} data-bs-toggle="tooltip" title="Use asymmetric unit, not Assembly ID."/>
                    </td>
                </tr>
                </tbody>
            </table>
        );
    }

    formByFile() {
        return (
            <table style={{ width: "100%" }}>
                <tbody>
                    <tr>
                        <td><label htmlFor="frm-jobSetup-setupForm-file">Upload file</label>:</td>
                        <td>
                            <input type="file" name="file" className="text" id="frm-jobSetup-setupForm-file" style = {{ maxWidth: '250px' }}
                                   accept=".pdb,.cif,.gz" data-bs-toggle="tooltip"
                                   title={`PDB (*.pdb) and mmCIF (*.cif) files, also gzipped (*.pdb.gz, *.cif.gz), at most ${MAX_UPLOAD_MB} MB, also when unpacked. Only the first model is used. E.g. cleaned PDB with only one chain and without unnecessary HETATMs.`}/>
                        </td>
                    </tr>
                </tbody>
            </table>
        );
    }

    getLastNSessions() {
        let sessions = [];

        for (let i = 0; i < LAST_N_SESSIONS_N; i++) {
            let session = getNthSession(i);
            if (session === "") {
                break;
            }
            sessions.push(
                <LastSession key={i} session={session} />
            );
        }

        if (sessions.length === 0) {
            sessions.push(
                <tr key="none">
                    <td colSpan={4}>
                        There are no last opened sessions available...
                    </td>
                </tr>
            );
        }

        return sessions;

    }

    formByLastNSessions() {
        let sessions = this.getLastNSessions();
        return (
            <table style={{ width: "100%" }} className="last-session-form">
                <thead>
                    <tr>
                        <th>
                            Created
                        </th>
                        <th>
                            PDB ID
                        </th>
                        <th>
                            Assembly ID
                        </th>
                        <th>
                            Submission
                        </th>
                    </tr>
                </thead>
                <tbody>
                    {sessions}
                </tbody>
            </table>
        );
    }
}

interface LastSessionProps { session: string }
interface LastSessionState { loaded: boolean, data: Session | null }
class LastSession extends React.Component<LastSessionProps, LastSessionState> {
    state: LastSessionState = { loaded: false, data: null }

    componentDidMount() {
        let computationId = this.props.session.split("|")[1].split("/")[0];

        SessionInfo.DataProvider.get(computationId, (_id, session) => {
            this.setState({
                loaded: true,
                data: session
            });
        });
    }

    render() {
        // "date|computationId[/submitId]"
        const [date, sessionUrl] = this.props.session.split("|");
        const open = () => location.replace(`/online/${sessionUrl}`);
        const info = this.state.data;
        if (!this.state.loaded || info === null) {
            return <tr onClick={open} className="linkLike">
                <td>{date}</td>
                <td colSpan={3}>{sessionUrl}</td>
            </tr>;
        }
        // a PDB entry loaded without an assembly is its asymmetric unit; an uploaded structure has no assembly
        const assemblyId = info.assembly_id ?? (info.pdb_id !== null ? "Asymmetric unit" : "");
        return <tr onClick={open} className="linkLike">
            <td>{date}</td>
            <td>{info.pdb_id ?? "User structure"}</td>
            <td>{assemblyId}</td>
            <td>{sessionUrl.split("/")[1]}</td>
        </tr>;
    }
}