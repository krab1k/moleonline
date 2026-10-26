import { CommonOptions } from "../config/common";
import { ApiService, CSAResidues, Cofactors, isDone, Job, Session } from "./MoleAPIService";
import { describeError, isRefused } from "./Http";
import {Tunnel} from "./DataInterface";

type DataHandler<T> = (key: string, data: T) => void;
type DataErrorHandler = (key: string, error: string) => void;

// Caches API data per key and notifies handlers once it arrives.
// Concurrent requests for a key share one fetch; only network and server errors are retried (with backoff),
// a request the API refuses (e.g. 404 unknown computation) fails at once, and handler exceptions are not
// treated as request failures.
class KeyedDataProvider<T> {
    private static readonly MAX_RETRIES = 5;
    private data = new Map<string, T>();
    private handlers: { key: string, handler: DataHandler<T>, onError?: DataErrorHandler, stayForUpdate: boolean }[] = [];
    private pending = new Set<string>();

    constructor(
        private fetch: (key: string) => Promise<T>,
        private failedMessage: string,
    ) { }

    public get(key: string, handler: DataHandler<T>, onlyFresh?: boolean, onError?: DataErrorHandler) {
        const data = this.data.get(key);
        if (data !== void 0 && !onlyFresh) {
            handler(key, data);
            return;
        }
        this.attachHandler(key, handler, false, onError);
    }

    // Returns a function that unsubscribes the handler again (e.g. when a component unmounts)
    public subscribe(key: string, handler: DataHandler<T>, onlyFresh?: boolean): () => void {
        const data = this.data.get(key);
        if (data !== void 0 && !onlyFresh) {
            handler(key, data);
        }
        const entry = this.attachHandler(key, handler, true);
        return () => { this.handlers = this.handlers.filter(h => h !== entry); };
    }

    private attachHandler(key: string, handler: DataHandler<T>, stayForUpdate: boolean, onError?: DataErrorHandler) {
        const entry = { key, handler, onError, stayForUpdate };
        this.handlers.push(entry);
        if (!this.pending.has(key)) {
            this.pending.add(key);
            this.fetchData(key, 0);
        }
        return entry;
    }

    private fetchData(key: string, attempt: number) {
        this.fetch(key).then((val) => {
            this.pending.delete(key);
            if (CommonOptions.DEBUG_MODE)
                console.log(val);
            this.data.set(key, val);
            this.runHandlers(key, val);
        }, (err) => {
            if (CommonOptions.DEBUG_MODE)
                console.log(err);
            if (!isRefused(err) && attempt < KeyedDataProvider.MAX_RETRIES) {
                window.setTimeout(() => this.fetchData(key, attempt + 1), 500 * 2 ** attempt);
            } else {
                this.pending.delete(key);
                this.runErrorHandlers(key, describeError(err, this.failedMessage));
            }
        });
    }

    private runHandlers(key: string, data: T) {
        const current = this.handlers.filter(h => h.key === key);
        this.handlers = this.handlers.filter(h => h.key !== key || h.stayForUpdate);
        for (const h of current) {
            h.handler(key, data);
        }
    }

    private runErrorHandlers(key: string, error: string) {
        const failed = this.handlers.filter(h => h.key === key);
        this.handlers = this.handlers.filter(h => h.key !== key || h.stayForUpdate);
        for (const h of failed) {
            h.onError?.(key, error);
        }
    }
}

export namespace SessionInfo {
    export const DataProvider = new KeyedDataProvider<Session>(
        sessionId => ApiService.getSession(sessionId),
        'Could not load the session from the MOLE API.',
    );

    // The cached session as a promise; rejects with the message for the user
    export function get(sessionId: string): Promise<Session> {
        return new Promise((res, rej) => DataProvider.get(sessionId, (_id, session) => res(session), false, (_id, error) => rej(error)));
    }
}

export namespace SessionStatus {
    // Poll often at first, then less often for long loads
    const FIRST_POLL_MS = 500;
    const MAX_POLL_MS = 5000;
    // A request that fails on the network or the server is retried after 2, 4, 8, 16 and 30 s before it rejects;
    // a refused one (404 unknown, 410 deleted) rejects at once
    const MAX_RETRIES = 5;
    const MAX_RETRY_MS = 30000;

    const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

    // The session once its load ended, ready or failed; onChange sees each state polled on the way
    export async function waitUntilLoaded(sessionId: string, onChange?: (session: Session) => void): Promise<Session> {
        let pollMs = FIRST_POLL_MS;
        let failures = 0;
        for (;;) {
            let session: Session;
            try {
                session = await ApiService.getSession(sessionId);
            } catch (err) {
                if (isRefused(err) || failures >= MAX_RETRIES) {
                    throw err;
                }
                await sleep(Math.min(2000 * 2 ** failures++, MAX_RETRY_MS));
                continue;
            }
            failures = 0;
            onChange?.(session);
            if (session.state === "ready" || session.state === "failed") {
                return session;
            }
            await sleep(pollMs);
            pollMs = Math.min(pollMs * 1.5, MAX_POLL_MS);
        }
    }
}

export namespace JobStatus {
    export type OnStatusChangeHandler = (job: Job) => void;
    export type OnErrHandler = (err: any) => void;
    export class Watcher {
        private static handlers: Map<String, OnStatusChangeHandler[]>
        private static errHandlers: Map<String, OnErrHandler[]>
        private static makeHash(computationId: string, submitId: number) {
            return `${computationId}:${submitId}`;
        }

        private static registerErrHandler(computationId: string, submitId: number, handler: OnErrHandler) {
            if (this.errHandlers === void 0) {
                this.errHandlers = new Map<string, OnErrHandler[]>();
            }
            let key = this.makeHash(computationId, submitId);
            let handlers = this.errHandlers.get(key);

            if (handlers === void 0) {
                handlers = [];
            }

            handlers.push(
                handler
            );
            this.errHandlers.set(key, handlers);
        }

        public static registerOnChangeHandler(computationId: string, submitId: number, handler: OnStatusChangeHandler, onErr: OnErrHandler) {
            if (this.handlers === void 0) {
                this.handlers = new Map<string, OnStatusChangeHandler[]>();
            }
            let key = this.makeHash(computationId, submitId);
            let handlers = this.handlers.get(key);
            let shouldStartLoop = false;
            if (handlers === void 0) {
                handlers = [];
                shouldStartLoop = true;
            }

            handlers.push(
                handler
            );
            this.handlers.set(key, handlers);

            this.registerErrHandler(computationId, submitId, onErr);

            if (shouldStartLoop) {
                this.waitForResult(computationId, submitId);
            }
        }

        private static notifyStatusUpdate(computationId: string, submitId: number, status: Job) {
            let handlers = this.handlers.get(this.makeHash(computationId, submitId));
            if (handlers === void 0) {
                return;
            }

            // a failing subscriber must not stop the others, nor the polling and its cleanup
            for (let h of handlers) {
                try {
                    h(status);
                } catch (e) {
                    console.error(`Status handler of ${computationId}:${submitId} failed`, e);
                }
            }
        }

        private static removeHandlers(computationId: string, submitId: number) {
            let key = this.makeHash(computationId, submitId);
            this.handlers.delete(key);
            this.errHandlers.delete(key);
        }

        // Poll often at first, then less often for long jobs
        private static readonly FIRST_POLL_MS = 1000;
        private static readonly MAX_POLL_MS = 5000;
        // A status request that fails on the network or the server is retried after 2, 4, 8, 16 and 30 s
        // before the error is reported
        private static readonly MAX_RETRIES = 5;
        private static readonly MAX_RETRY_MS = 30000;

        private static waitForResult(computationId: string, submitId: number, pollMs = Watcher.FIRST_POLL_MS, failures = 0) {
            ApiService.getJob(computationId, submitId).then((job) => {
                if (CommonOptions.DEBUG_MODE)
                    console.log(job);
                this.notifyStatusUpdate(computationId, submitId, job);
                if (isDone(job.state)) {
                    this.removeHandlers(computationId, submitId);
                } else {
                    window.setTimeout(() => {
                        this.waitForResult(computationId, submitId, Math.min(pollMs * 1.5, Watcher.MAX_POLL_MS));
                    }, pollMs);
                }
            }, (err) => { // only a failed request is retried, not an error in the code above
                // a refused request (404 unknown session or job, 410 deleted) is final
                if (!isRefused(err) && failures < Watcher.MAX_RETRIES) {
                    const retryMs = Math.min(2000 * 2 ** failures, Watcher.MAX_RETRY_MS);
                    if (CommonOptions.DEBUG_MODE)
                        console.log(`Status of ${computationId}:${submitId} not available, retrying in ${retryMs} ms`, err);
                    window.setTimeout(() => {
                        this.waitForResult(computationId, submitId, pollMs, failures + 1);
                    }, retryMs);
                    return;
                }

                // give up; a new registration starts polling again
                let h = this.errHandlers.get(this.makeHash(computationId, submitId));
                this.removeHandlers(computationId, submitId);
                if (h === void 0) {
                    throw new Error(err);
                }

                for (let handler of h) {
                    handler(err);
                }
            });
        }
    }
}

export namespace DataProxyCSAResidues {
    export const DataProvider = new KeyedDataProvider<CSAResidues>(
        sessionId => ApiService.getActiveSites(sessionId),
        'Could not load CSA residues from the MOLE API.',
    );
}

export namespace DataProxyCofactors {
    const provider = new KeyedDataProvider<Cofactors>(
        () => ApiService.getCofactors(),
        'Could not load cofactors from the MOLE API.',
    );

    export const DataProvider = {
        get(handler: (info: Cofactors) => void, onlyFresh?: boolean) {
            provider.get('', (_key, info) => handler(info), onlyFresh);
        },
    };
}

export namespace TwoDProts {
    export type OnStatusChangeHandler = (status: string, jobId: string, errorMsg: string) => void;
    export type OnErrorHandler = (message: string) => void;

    const SERVICE = "2DProts (2dprots.ncbr.muni.cz)";
    const REQUEST_SECONDS = 30; // each request, without its answer in time it failed
    const JOB_MINUTES = 10; // a job not finished by then is given up
    const POLL_MS = 1000;
    const POLL_FAILURES = 5; // consecutive failed status checks: one alone may be a hiccup

    class ServiceError extends Error {
        constructor(readonly status: number) {
            super(`HTTP ${status}`);
        }
    }

    // What the user is told about a failed request to 2DProts
    export function describeFailure(error: unknown): string {
        if (error instanceof ServiceError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) {
            return `${SERVICE} refused the request (HTTP ${error.status}).`;
        }
        if (error instanceof TypeError || error instanceof ServiceError
            || (error instanceof DOMException && error.name === "TimeoutError")) {
            return `${SERVICE} is not available at the moment. Please, try again later.`;
        }
        return `${SERVICE} failed: ${error instanceof Error ? error.message : String(error)}`;
    }

    async function request(url: string, stop: AbortSignal, init: RequestInit = {}): Promise<any> {
        const response = await fetch(url, { ...init, signal: AbortSignal.any([stop, AbortSignal.timeout(REQUEST_SECONDS * 1000)]) });
        if (!response.ok) {
            throw new ServiceError(response.status);
        }
        return response.json();
    }

    // The picture of a finished job: the SVG's text
    export async function picture(jobId: string): Promise<string> {
        const response = await fetch(`https://2dprots.ncbr.muni.cz/static/2DProt/custom_jobs/${jobId}/output.svg`,
                                     { signal: AbortSignal.timeout(REQUEST_SECONDS * 1000) });
        if (!response.ok) {
            throw new ServiceError(response.status);
        }
        return response.text();
    }

    export class Watcher {
        private static baseURL = 'https://2dprots.ncbr.muni.cz/api/v1/custom_protein';
        private static statusURL = (jobId: string) => `https://2dprots.ncbr.muni.cz/api/v1/custom_protein/${jobId}`;

        // Starts a 2DProts job and follows it until it succeeded or failed; returns a function that stops following
        // it, also before 2DProts answered. onError gets the message for the user; nothing is reported after a stop.
        public static startJob(
            proteinUrl: string,
            channels: (Tunnel)[], // Local channels object
            onStatusChange: OnStatusChangeHandler,
            onError: OnErrorHandler
        ): () => void {
            const stop = new AbortController();
            (async () => {
                const data = await request(this.baseURL, stop.signal, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        protein_url: proteinUrl,
                        channels: channels,
                    }),
                });
                if (!data.job_id) {
                    throw new Error('it did not start the computation.');
                }
                await this.monitorJob(data.job_id, stop.signal, onStatusChange);
            })().catch(error => {
                if (!stop.signal.aborted) {
                    console.error('2DProts:', error);
                    onError(describeFailure(error));
                }
            });
            return () => stop.abort();
        }

        private static async monitorJob(jobId: string, stop: AbortSignal, onStatusChange: OnStatusChangeHandler) {
            const end = Date.now() + JOB_MINUTES * 60 * 1000;
            let failures = 0;
            while (!stop.aborted) {
                try {
                    const data = await request(this.statusURL(jobId), stop);
                    failures = 0;
                    if (stop.aborted) {
                        return;
                    }
                    onStatusChange(data.job_state, jobId, data.job_error);
                    if (data.job_state === 'SUCCESS' || data.job_state === 'FAILURE') {
                        return;
                    }
                } catch (error) {
                    if (stop.aborted || ++failures >= POLL_FAILURES) {
                        throw error;
                    }
                }
                if (Date.now() > end) {
                    throw new Error(`it did not finish the computation within ${JOB_MINUTES} minutes.`);
                }
                await new Promise(resolve => setTimeout(resolve, POLL_MS));
            }
        }
    }
}
