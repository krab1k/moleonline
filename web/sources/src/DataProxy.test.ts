import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RequestError } from './Http';

// The API, replaced: each test sets what getSession answers
const api = vi.hoisted(() => ({ getSession: vi.fn() }));
vi.mock('./MoleAPIService', () => ({ ApiService: api }));

import { SessionStatus } from './DataProxy';

const session = (state: string, error = '') => ({
    id: 'abc', state, error, pdb_id: '1tqn', assembly_id: null, created: null, loaded: null,
});

describe('SessionStatus.waitUntilLoaded', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        api.getSession.mockReset();
    });
    afterEach(() => vi.useRealTimers());

    it('polls until the session is ready, reporting each state', async () => {
        api.getSession
            .mockResolvedValueOnce(session('queued'))
            .mockResolvedValueOnce(session('loading'))
            .mockResolvedValueOnce(session('ready'));
        const seen: string[] = [];
        const done = SessionStatus.waitUntilLoaded('abc', s => seen.push(s.state));
        await vi.runAllTimersAsync();
        expect((await done).state).toBe('ready');
        expect(seen).toEqual(['queued', 'loading', 'ready']);
        expect(api.getSession).toHaveBeenCalledWith('abc');
    });

    it('resolves with a failed session, which says why', async () => {
        api.getSession.mockResolvedValueOnce(session('loading')).mockResolvedValueOnce(session('failed', 'Entry not found.'));
        const done = SessionStatus.waitUntilLoaded('abc');
        await vi.runAllTimersAsync();
        expect(await done).toMatchObject({ state: 'failed', error: 'Entry not found.' });
    });

    it('retries a network or server error, then goes on', async () => {
        api.getSession.mockRejectedValueOnce(new RequestError('Bad Gateway', 502, false)).mockResolvedValueOnce(session('ready'));
        const done = SessionStatus.waitUntilLoaded('abc');
        await vi.runAllTimersAsync();
        expect((await done).state).toBe('ready');
        expect(api.getSession).toHaveBeenCalledTimes(2);
    });

    it('rejects at once when the API refuses (deleted, unknown)', async () => {
        api.getSession.mockRejectedValue(new RequestError('The computation has been deleted.', 410, true));
        const done = SessionStatus.waitUntilLoaded('abc');
        await expect(done).rejects.toThrow('The computation has been deleted.');
        expect(api.getSession).toHaveBeenCalledTimes(1);
    });

    it('rejects after the retries fail', async () => {
        api.getSession.mockRejectedValue(new RequestError('Bad Gateway', 502, false));
        const done = SessionStatus.waitUntilLoaded('abc');
        const failed = expect(done).rejects.toThrow('Bad Gateway');
        await vi.runAllTimersAsync();
        await failed;
        expect(api.getSession).toHaveBeenCalledTimes(6); // the first request and 5 retries
    });
});
