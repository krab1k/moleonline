import { describe, expect, it } from 'vitest';
import { centreLineOf, closestPoint } from './centre-line';

describe('closestPoint', () => {
    const line = centreLineOf([{ X: 0, Y: 0, Z: 0 }, { X: 1, Y: 0, Z: 0 }, { X: 2, Y: 0, Z: 0 }, { X: 2, Y: 0, Z: 0 }]);

    it('finds the centreline point closest to a position', () => {
        expect(closestPoint(line, 0.9, 3, -1)).toBe(1);
        expect(closestPoint(line, -5, 0, 0)).toBe(0);
    });

    it('takes the first of equally close points', () => {
        expect(closestPoint(line, 0.5, 0, 0)).toBe(0);
        expect(closestPoint(line, 7, 0, 0)).toBe(2);
    });
});
