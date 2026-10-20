import React from "react";
import { ApiService, isDone, MoleConfig, PoresConfig } from "../../MoleAPIService";
import { describeError } from "../../Http";
import { Events } from "../../Bridge";
import { JobStatus, SessionStatus } from "../../DataProxy";

export class Examples extends React.Component<{}, { activeButton: number }> {
    state = { activeButton: -1 }; // the example being started

    // Loads the example's structure, then submits its job
    private async handleFormSubmit(pdbId: string) {
        let session;
        try {
            session = await SessionStatus.waitUntilLoaded((await ApiService.createSession(pdbId, "preferred")).id);
        } catch (reason) {
            this.showError(describeError(reason, "The MOLE API could not be reached, please try again later."));
            return;
        }
        if (session.state === "failed") {
            this.showError(`The structure could not be loaded: ${session.error}`);
            return;
        }
        await this.loadSumbission(session.id);
    }

    private async loadSumbission(computationId: string) {
        try {
            const response = await fetch(`/static/${this.examples[this.state.activeButton].id}.json`);
            if (!response.ok) {
                throw new Error(`Failed to fetch parameters`)
            }
            // the example's parameters: its Mode (Mole, Pores) and Config, as the web exports them
            const data = await response.json();
            const job = await ApiService.createJob(computationId, data.Mode === "Mole"
                ? { type: "tunnels", parameters: data.Config as MoleConfig }
                : { type: "pores", parameters: data.Config as PoresConfig });

            // the example's page once the job is done
            JobStatus.Watcher.registerOnChangeHandler(computationId, job.number, (current) => {
                if (isDone(current.state)) {
                    location.replace(`/online/${computationId}/${job.number}`);
                }
            }, (err) => this.showError(describeError(err, "Job status cannot be tracked. Please try to refresh the page.")));
        } catch (error: any) {
            console.error('The example could not be started:', error);
            this.showError(describeError(error, "The example could not be started."));
        }
    }

    private showError(message: string) {
        this.setState({ activeButton: -1 });
        Events.invokeNotifyMessage({
            messageType: "Danger",
            message
        });
    }

    examples = [
        { id: '1GPN', src: '/images/example_1GPN.png', title: '1GPN - complex of Tetronarce californica Acetylcholinesterase (AChE) with huperzine B - potential Alzheimer drug' },
        { id: '1V9E', src: '/images/example_1V9E.png', title: '1V9E - bovine Carbonic anhydrase 2 (CA2)' },
        { id: '2V61', src: '/images/example_2V61.png', title: '2V61 - human Amine oxidase B (MAOB) in complex with selective coumarine derivative inhibitor' },
        { id: '2VV5', src: '/images/example_2VV5.png', title: '2VV5 - open form of an Escherichia coli mechanosensitive channel MscS' },
        { id: '2XN8', src: '/images/example_2XN8.png', title: '2XN8 - substrate-free form of Mycobacterium tuberculosis Steroid C26-moooxygenase (cyp125)' },
        { id: '3LDC', src: '/images/example_3LDC.png', title: '3LDC - open form of Methanothermobacter thermautotrophicus Calcium-gated potassium channel MthK' },
    ];

    private renderExample(example: { id: string, src: string, title: string }, index: number) {
        const active = this.state.activeButton;
        return <div key={example.id} className="col d-flex flex-column justify-content-center align-items-center position-relative">
            {active === index && (
                <div className="spinner-overlay">
                    <div className="spinner-border text-primary" role="status">
                        <span className="visually-hidden">Loading...</span>
                    </div>
                </div>
            )}
            <figure
                className={`figure ${active !== -1 && active !== index ? 'disabled' : ''}`}
                style={{ maxWidth: '28rem', cursor: 'pointer' }}
                onClick={() => { this.setState({ activeButton: index }); this.handleFormSubmit(example.id); }}
                data-bs-toggle="tooltip"
                data-bs-placement="bottom"
                title={example.title}
            >
                <img
                    className="img-fluid figure-img"
                    style={{ objectFit: 'contain' }}
                    src={example.src}
                    alt={example.title}
                />
            </figure>
        </div>;
    }

    render() {
        return (
            <div className="container-fluid text-center">
                <div className="row">
                    <div className="col">
                        <h4><b>Examples</b></h4>
                    </div>
                </div>
                {/* two rows of three */}
                {[0, 3].map(start => <div key={start} className="row mb-3">
                    {this.examples.slice(start, start + 3).map((example, i) => this.renderExample(example, start + i))}
                </div>)}
            </div>
        );
    }
}
