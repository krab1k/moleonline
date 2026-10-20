import React from "react";
import { SessionInfo } from "../../../DataProxy";
import { getParameters } from "../../../Common/Util/Router";

interface State { pdbid?: string, err?: string };

export class PdbIdSign extends React.Component<{}, State> {
    state: State = { pdbid: void 0, err: void 0 };

    componentDidMount() {
        let params = getParameters();
        if (params === null) {
            this.setState({ err: "!!!" });
            return;
        }
        SessionInfo.get(params.computationId).then((res) => {
            if (res.pdb_id === null) {
                this.setState({ err: "---" });
            }
            else {
                this.setState({ pdbid: res.pdb_id });
            }
        })
            .catch(err => {
                this.setState({ err: "<Error>" });
            });
    }

    render() {
        if (this.state.pdbid === void 0) {
            return <div className="current-pbdbid-sign">{(this.state.err === void 0) ? "..." : this.state.err}</div>
        }

        return <div className="current-pbdbid-sign">
            <a href={`https://pdbe.org/${this.state.pdbid}`} target="_blank">{this.state.pdbid} <span className="bi bi-box-arrow-up-right href-ico"></span></a>
        </div>
    }
}  
