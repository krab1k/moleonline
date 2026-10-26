import { afterEach, describe, expect, it, vi } from 'vitest';
import { TwoDProts } from './DataProxy';

const UNAVAILABLE = '2DProts (2dprots.ncbr.muni.cz) is not available at the moment. Please, try again later.';
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

// Starts a job with the answers of fetch replaced; resolves with the first error message or the final status
function run(answers: ((init: RequestInit) => Promise<Response>)[]) {
    let call = 0;
    const fetch = vi.fn((_url: string, init: RequestInit) => answers[Math.min(call++, answers.length - 1)](init));
    vi.stubGlobal('fetch', fetch);
    return new Promise<{ error?: string, status?: string, stop: () => void }>(resolve => {
        const stop = TwoDProts.Watcher.startJob('https://example.org/structure', [],
            (status) => { if (status === 'SUCCESS' || status === 'FAILURE') resolve({ status, stop }); },
            (error) => resolve({ error, stop }));
    });
}

describe('2DProts', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it.each([
        ['no answer', () => Promise.reject(new TypeError('Failed to fetch'))],
        ['a server error', () => Promise.resolve(json(503, {}))],
        ['no answer in time', () => Promise.reject(new DOMException('timed out', 'TimeoutError'))],
    ])('says that 2DProts is not available: %s', async (_, answer) => {
        expect((await run([answer])).error).toBe(UNAVAILABLE);
    });

    it('tells a refusal by its status', async () => {
        expect((await run([async () => json(400, {})])).error).toBe('2DProts (2dprots.ncbr.muni.cz) refused the request (HTTP 400).');
    });

    it('goes on after a failed status check, as one alone may be a hiccup', async () => {
        const result = await run([
            async () => json(200, { job_id: 'j1' }),
            () => Promise.reject(new TypeError('Failed to fetch')),
            async () => json(200, { job_state: 'SUCCESS' }),
        ]);
        expect(result).toMatchObject({ status: 'SUCCESS' });
    });

    it('stops quietly when cancelled before 2DProts answered, the request aborted', async () => {
        let aborted = false;
        const onError = vi.fn();
        vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_, reject) =>
            init.signal?.addEventListener('abort', () => { aborted = true; reject(init.signal?.reason); }))));
        const stop = TwoDProts.Watcher.startJob('https://example.org/structure', [], () => {}, onError);
        stop();
        await new Promise(resolve => setTimeout(resolve, 10));
        expect(aborted).toBe(true);
        expect(onError).not.toHaveBeenCalled();
    });

    it("says why the job's picture could not be downloaded", async () => {
        vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 502 })));
        const error = await TwoDProts.picture('j1').catch(e => e);
        expect(TwoDProts.describeFailure(error)).toBe(UNAVAILABLE);
    });
});
