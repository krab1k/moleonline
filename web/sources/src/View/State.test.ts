import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tunnel, TunnelMetaInfo } from "../DataInterface";

// the plugin is stubbed: each render resolves when the test says so
const plugin = vi.hoisted(() => ({
    pending: [] as { resolve: () => void, reject: (e: unknown) => void }[],
    rendered: new Set<string>(),
    nextRef: 0,
}));

vi.mock("./Context", () => {
    const render = () => new Promise<[unknown, string]>((resolve, reject) => {
        const ref = `ref${plugin.nextRef++}`;
        plugin.pending.push({
            resolve: () => { plugin.rendered.add(ref); resolve([{}, ref]); },
            reject,
        });
    });
    return { Context: { getInstance: () => ({ plugin: { state: { data: {} } }, renderTunnel: render, renderPropertyColorTunnel: render }) } };
});

vi.mock("molstar/lib/mol-plugin/commands", () => ({
    PluginCommands: { State: { RemoveObject: async (_: unknown, { ref }: { ref: string }) => { plugin.rendered.delete(ref); } } },
}));

// no 2D view to highlight
vi.stubGlobal("document", { getElementById: () => null });
// the page events go to a bare event target, and a frame is a timer
vi.stubGlobal("window", new EventTarget());
vi.stubGlobal("requestAnimationFrame", (callback: () => void) => setTimeout(callback));

const { showChannelVisuals, showChannelPropertyColorVisuals, isRenderingChannels } = await import("./State");
const { listen } = await import("./CommonUtils/Events");

type Channel = Tunnel & TunnelMetaInfo;
const channel = () => ({ Id: "c1", __color: 0 } as unknown as Channel);

// resolves each render once it is requested, until the operations finish
async function settle(...operations: Promise<unknown>[]) {
    let done = false;
    const all = Promise.allSettled(operations).finally(() => { done = true; });
    while (!done) {
        await new Promise(r => setTimeout(r, 0));
        plugin.pending.shift()?.resolve();
    }
    for (const result of await all) {
        if (result.status === "rejected") throw result.reason;
    }
}

describe("channel visual operations", () => {
    beforeEach(() => {
        plugin.pending = [];
        plugin.rendered.clear();
        plugin.nextRef = 0;
    });

    it("renders a channel shown twice at once only once", async () => {
        const c = channel();
        await settle(showChannelVisuals([c], true), showChannelVisuals([c], true));
        expect([...plugin.rendered]).toEqual([c.__ref]);

        await showChannelVisuals([c], false);
        expect(plugin.rendered.size).toBe(0);
        expect(c.__isVisible).toBe(false);
    });

    it("keeps only the last visual of overlapping repaints", async () => {
        const c = channel();
        await settle(showChannelVisuals([c], true));
        await settle(showChannelVisuals([c], true, true), showChannelPropertyColorVisuals(c, {} as any, true), showChannelVisuals([c], true, true));
        expect([...plugin.rendered]).toEqual([c.__ref]);
    });

    it("leaves a channel whose render failed hidden and showable", async () => {
        const c = channel();
        const failed = showChannelVisuals([c], true);
        while (plugin.pending.length === 0) await new Promise(r => setTimeout(r, 0));
        plugin.pending.shift()!.reject(new Error("render failed"));
        await expect(failed).rejects.toThrow("render failed");
        expect(c.__isVisible).toBeFalsy();
        expect(c.__isBusy).toBe(false);

        await settle(showChannelVisuals([c], true));
        expect([...plugin.rendered]).toEqual([c.__ref]);
        expect(c.__isVisible).toBe(true);
    });

    it("announces the rendering once for overlapping operations, until all of them finish", async () => {
        const announced: boolean[] = [];
        const unsubscribe = listen("channelsRendering", isRendering => announced.push(isRendering));
        const a = channel(), b = { ...channel(), Id: "c2" } as Channel;
        const operations = [showChannelVisuals([a], true), showChannelVisuals([b], true)];
        expect(isRenderingChannels()).toBe(true);
        await settle(...operations);
        expect(announced).toEqual([true, false]);
        expect(isRenderingChannels()).toBe(false);

        // hiding renders nothing
        await showChannelVisuals([a, b], false);
        expect(announced).toEqual([true, false]);
        unsubscribe();
    });

    it("ends the announcement when a render fails", async () => {
        const announced: boolean[] = [];
        const unsubscribe = listen("channelsRendering", isRendering => announced.push(isRendering));
        const failed = showChannelVisuals([channel()], true);
        while (plugin.pending.length === 0) await new Promise(r => setTimeout(r, 0));
        plugin.pending.shift()!.reject(new Error("render failed"));
        await expect(failed).rejects.toThrow("render failed");
        expect(announced).toEqual([true, false]);
        unsubscribe();
    });

    it("renders without animation frames", async () => {
        // a page without frames: the timer ends the wait for the paint
        vi.stubGlobal("requestAnimationFrame", () => 0);
        try {
            const c = channel();
            await settle(showChannelVisuals([c], true));
            expect([...plugin.rendered]).toEqual([c.__ref]);
        } finally {
            vi.stubGlobal("requestAnimationFrame", (callback: () => void) => setTimeout(callback));
        }
    });

    it("does not wait for a paint on a hidden page", async () => {
        const frames = vi.fn();
        vi.stubGlobal("requestAnimationFrame", frames);
        vi.stubGlobal("document", { getElementById: () => null, hidden: true });
        try {
            const c = channel();
            const shown = showChannelVisuals([c], true);
            await new Promise(r => setTimeout(r, 0));
            expect(plugin.pending).toHaveLength(1);
            await settle(shown);
            expect(frames).not.toHaveBeenCalled();
        } finally {
            vi.stubGlobal("requestAnimationFrame", (callback: () => void) => setTimeout(callback));
            vi.stubGlobal("document", { getElementById: () => null });
        }
    });
});
