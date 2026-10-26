import { afterEach, describe, expect, it, vi } from 'vitest';

// the panel's surroundings: the session's address, the shown channels, the window
vi.mock('../../../Common/Util/Router', () => ({ getParameters: () => ({ computationId: 'c1' }) }));
vi.mock('../../CommonUtils/TwoDProtsBridge', () => ({ TwoDProtsBridge: { getVizualizedChannels: () => [] } }));
vi.stubGlobal('window', { location: { origin: 'http://localhost' } });

const { TwoDProts: Proxy } = await import('../../../DataProxy');
const { TwoDProts: Panel } = await import('./UI');

type Status = (status: string, jobId: string, errorMsg: string) => void;

// The panel without React: its state changes at once; every started job's status handler, every download's settlers
function panel() {
    const p = new Panel({});
    p.setState = ((s: any) => Object.assign(p.state, s)) as any;
    const jobs: Status[] = [];
    vi.spyOn(Proxy.Watcher, 'startJob').mockImplementation((_url, _channels, onStatus) => { jobs.push(onStatus); return () => {}; });
    const downloads = new Map<string, { resolve: (svg: string) => void, reject: (e: unknown) => void }>();
    vi.spyOn(Proxy, 'picture').mockImplementation(jobId => new Promise((resolve, reject) => downloads.set(jobId, { resolve, reject })));
    const start = () => (p as any).startComputation();
    return { p, jobs, downloads, start };
}

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

describe('the 2DProts panel', () => {
    afterEach(() => vi.restoreAllMocks());

    it("ignores an older computation's late picture, status and failure", async () => {
        const { p, jobs, downloads, start } = panel();
        await start();
        jobs[0]('SUCCESS', 'A', '');      // A finished, its picture is on its way
        await start();
        jobs[1]('SUCCESS', 'B', '');      // so did B, a newer one, its picture too
        jobs[0]('FAILURE', 'A', 'late');   // a late status of A
        downloads.get('A')!.reject(new TypeError('Failed to fetch'));
        await flush();
        expect(p.state).toMatchObject({ jobId: 'B', error: undefined, isComputing: false });
    });

    it("ignores a cancelled computation's picture", async () => {
        const { p, jobs, downloads, start } = panel();
        await start();
        jobs[0]('SUCCESS', 'A', '');
        (p as any).cancelComputation();
        downloads.get('A')!.resolve('<svg/>');
        await flush();
        expect(p.state).toMatchObject({ jobId: '', error: undefined, canceled: true });
    });

    it("tells the current computation's failed download", async () => {
        const { p, jobs, downloads, start } = panel();
        await start();
        jobs[0]('SUCCESS', 'A', '');
        downloads.get('A')!.reject(new TypeError('Failed to fetch'));
        await flush();
        expect(p.state.jobId).toBe('');
        expect(JSON.stringify((p.state.error as any).props.children)).toContain('is not available at the moment');
    });
});
