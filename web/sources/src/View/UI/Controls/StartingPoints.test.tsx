import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiService } from '../../../PatternQueryAPIService';
import { StartingPointQueryBox } from './StartingPoints';

// The box without React: its state changes at once, its input's onChange is the typing
function box() {
    const b = new StartingPointQueryBox({ label: '', tooltip: '' });
    b.setState = ((s: any) => Object.assign(b.state, s)) as any;
    const type = (value: string) => (b.render() as any).props.children[0].props.onChange({ value });
    return { b, type };
}

const settle = async () => { await vi.advanceTimersByTimeAsync(500); };

describe('the PatternQuery starting point', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

    it('is checked once typing paused, for the last text only', async () => {
        const check = vi.spyOn(ApiService, 'getValidationResult').mockResolvedValue({ isOk: true });
        const { b, type } = box();
        type("R"); type("Res"); type("Residues('GOL')");
        expect(b.state.validationMessage).toBe('Validation in progress... Please wait.');
        await settle();
        expect(check).toHaveBeenCalledTimes(1);
        expect(check).toHaveBeenCalledWith("Residues('GOL')");
        expect(b.state).toMatchObject({ isValid: true, validationInProgress: false });
    });

    it('ignores the answer for a text that changed meanwhile', async () => {
        let answerOld!: (r: any) => void;
        vi.spyOn(ApiService, 'getValidationResult')
            .mockReturnValueOnce(new Promise(r => answerOld = r))
            .mockResolvedValue({ isOk: false, error: 'Unknown function.' });
        const { b, type } = box();
        type("Residues('GOL')");
        await settle(); // its check is on its way
        type("Bad('x')");
        await settle();
        answerOld({ isOk: true }); // the old text's answer comes last
        await settle();
        expect(b.state).toMatchObject({ value: "Bad('x')", isValid: false, validationMessage: 'Unknown function.' });
    });

    it('says when the query is not valid without a reason, and when the service is not available', async () => {
        const check = vi.spyOn(ApiService, 'getValidationResult').mockResolvedValue({ isOk: false });
        const { b, type } = box();
        type("x");
        await settle();
        expect(b.state.validationMessage).toBe('The query is not valid.');
        check.mockRejectedValue(new TypeError('Failed to fetch'));
        type("y");
        await settle();
        expect(b.state.validationMessage)
            .toBe('The query cannot be checked, PatternQuery (webchem.ncbr.muni.cz) is not available at the moment. Please, try again later.');
    });
});
