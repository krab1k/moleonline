import { describe, expect, it } from 'vitest';
import { Vizualizer } from './Vizualizer';

const getColor = Vizualizer.prototype.getColor;
const rgb = (s: string) => s.match(/\d+/g)!.map(Number);

const BLUE = { r: 0, g: 0, b: 255 }, LIGHT_BLUE = { r: 200, g: 200, b: 255 };
const LIGHT_YELLOW = { r: 255, g: 255, b: 200 }, YELLOW = { r: 255, g: 255, b: 0 };

describe('getColor', () => {
    // Polarity on its absolute scale: 0, centre 5, 52; each half has its own middle colour, as the real palettes
    const settings = {
        minVal: 0, maxVal: 52, centerPosition: 5, centerAbsolute: true, skipMiddle: false,
        minColor: BLUE, minColorMiddle: LIGHT_BLUE, maxColorMiddle: LIGHT_YELLOW, maxColor: YELLOW,
    };

    it('grades a value above the centre between the upper middle colour and the maximum colour', () => {
        // 20 is below the midpoint of 0 and 52, but above the centre
        expect(rgb(getColor(20, settings))).toEqual([255, 255, Math.floor(200 * (1 - 15 / 47))]);
    });

    it('grades each half towards its own middle colour', () => {
        // below the centre: blue to light blue, not to the upper half's light yellow
        expect(rgb(getColor(2.5, settings))).toEqual([100, 100, 255]);
        expect(rgb(getColor(4.99, settings))[2]).toBe(255);
        expect(getColor(5, settings)).toBe('rgb(255,255,200)'); // the upper half starts at the centre
    });

    it('keeps the ends', () => {
        expect(getColor(0, settings)).toBe('rgb(0,0,255)');
        expect(getColor(52, settings)).toBe('rgb(255,255,0)');
    });

    it('gives values outside the range the colours of its ends', () => {
        expect(getColor(-10, settings)).toBe('rgb(0,0,255)');
        expect(getColor(80, settings)).toBe('rgb(255,255,0)');
    });

    it('centres a relative scale at its midpoint', () => {
        const relative = { ...settings, minVal: -4, maxVal: 4, centerAbsolute: false };
        expect(getColor(-2, relative)).toBe('rgb(100,100,255)');
        expect(getColor(2, relative)).toBe('rgb(255,255,100)');
    });
});
