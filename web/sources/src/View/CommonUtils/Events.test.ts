import { describe, expect, it, vi } from 'vitest';
import { emit, emitResize, listen } from './Events';

// node has no window; any event target dispatches the same way
vi.stubGlobal('window', new EventTarget());

describe('page events', () => {
    it('passes the detail to the handler', () => {
        const handler = vi.fn();
        listen('layerSelected', handler);
        emit('layerSelected', [2, 5]);
        expect(handler).toHaveBeenCalledWith([2, 5]);
    });

    it('stops calling the handler once unsubscribed', () => {
        const handler = vi.fn();
        const unsubscribe = listen('contentResize', handler);
        emit('contentResize');
        unsubscribe();
        emit('contentResize');
        expect(handler).toHaveBeenCalledTimes(1);
    });

    it('reaches listeners added without the module', () => {
        // Mol* listens to the window directly
        const contentResize = vi.fn(), resize = vi.fn();
        window.addEventListener('contentResize', contentResize);
        window.addEventListener('resize', resize);
        emit('contentResize');
        emitResize();
        expect(contentResize).toHaveBeenCalledTimes(1);
        expect(resize).toHaveBeenCalledTimes(1);
    });
});
