import { describe, expect, it } from 'vitest';
import { channelsDBNotices } from './ChannelsDBAPIService';
import { RequestError } from './Http';

const ok = <T>(value: T): PromiseSettledResult<T> => ({ status: 'fulfilled', value });
const failed = (reason: unknown): PromiseSettledResult<never> => ({ status: 'rejected', reason });
const channels = ok({ Tunnels: [{ Id: '1' }] } as any);
const residues = ok(new Map());
const notFound = () => failed(new RequestError('GET: https://channelsdb/x 404: Not Found', 404, false));

describe('channelsDBNotices', () => {
    it('says nothing when ChannelsDB answered', () => {
        expect(channelsDBNotices('1tqn', channels, residues)).toEqual([]);
    });

    it.each([
        ['no such entry', notFound(), notFound()],
        ['no channels in the entry', ok({ Tunnels: [], Pores: [] } as any), residues],
    ])('tells, as information, that ChannelsDB has no channels: %s', (_, answer, annotations) => {
        expect(channelsDBNotices('1tqn', answer, annotations)).toEqual([
            { messageType: 'Info', message: 'ChannelsDB has no channels for 1TQN.' },
        ]);
    });

    it.each([
        ['no answer', new TypeError('Failed to fetch')],
        ['no answer in time', new RequestError('GET: https://channelsdb/x gave no answer within 15 s', 0, false)],
        ['a server error', new RequestError('GET: https://channelsdb/x 503: Service Unavailable', 503, false)],
    ])('warns once that ChannelsDB is not available, computing goes on: %s', (_, reason) => {
        expect(channelsDBNotices('1tqn', failed(reason), failed(reason))).toEqual([{
            messageType: 'Warning',
            message: 'ChannelsDB is not available at the moment, its channels and residue annotations for 1TQN are not shown. '
                + "Channels can be computed here as usual; reload the page later to see ChannelsDB's.",
        }]);
    });

    it('names only the channels when the residue annotations came', () => {
        const [notice] = channelsDBNotices('1tqn', failed(new TypeError('Failed to fetch')), residues);
        expect(notice.message).toMatch(/^ChannelsDB is not available at the moment, its channels for 1TQN are not shown\./);
    });

    it('warns about the residue annotations alone when only they failed', () => {
        expect(channelsDBNotices('1tqn', channels, failed(new TypeError('Failed to fetch')))).toEqual([{
            messageType: 'Warning', message: 'ChannelsDB is not available at the moment, its residue annotations for 1TQN are not shown.',
        }]);
        expect(channelsDBNotices('1tqn', channels, notFound())).toEqual([]);
    });

    it('tells a refusal by its status, and data that could not be read by the error', () => {
        expect(channelsDBNotices('1tqn', failed(new RequestError('GET: https://channelsdb/x 400: Bad Request', 400, false)), residues))
            .toEqual([{ messageType: 'Warning', message: 'ChannelsDB refused to give its channels for 1TQN (HTTP 400). Channels can be computed here as usual.' }]);
        expect(channelsDBNotices('1tqn', failed(new SyntaxError('Unexpected token <')), residues))
            .toEqual([{ messageType: 'Warning', message: "ChannelsDB's channels for 1TQN could not be shown (SyntaxError: Unexpected token <). Channels can be computed here as usual." }]);
    });
});
