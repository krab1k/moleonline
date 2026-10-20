/**
 * Togglers and sizes of the panels of the detail page, which fill the space available in their tabs.
 */

import { emit, emitResize, listen } from './Events';

function element(selector: string) {
    return document.querySelector<HTMLElement>(selector);
}

function toggleClass(selector: string, className: string) {
    document.querySelectorAll(selector).forEach(e => e.classList.toggle(className));
}

function styleNumber(e: Element, property: string) {
    const number = parseFloat(getComputedStyle(e).getPropertyValue(property));
    return isNaN(number) ? 0 : number;
}

function sumStyleNumbers(e: Element, properties: string[]) {
    return properties.reduce((sum, property) => sum + styleNumber(e, property), 0);
}

// Size of the content, without padding and border
function contentSize(e: Element, dimension: 'width' | 'height') {
    let size = styleNumber(e, dimension);
    if (getComputedStyle(e).boxSizing === 'border-box') {
        size -= dimension === 'width'
            ? sumStyleNumbers(e, ['padding-left', 'padding-right', 'border-left-width', 'border-right-width'])
            : sumStyleNumbers(e, ['padding-top', 'padding-bottom', 'border-top-width', 'border-bottom-width']);
    }
    return size;
}

function isShown(e: Element) {
    return e.classList.contains('show');
}

// Fills the given percentage of the tabs' height below their navigation, and their width
function fillSpaceOnResize(elementId: string, highLevelContainerId: string, height = 100) {
    const filled = element(`#${elementId}`);
    const parent = element(`#${highLevelContainerId}`);
    const nav = element(`#${highLevelContainerId} ul.nav.nav-tabs`);
    if (filled === null || parent === null || nav === null) {
        return;
    }

    const horizontalSpacing = ['padding-left', 'padding-right', 'margin-left', 'margin-right'];
    const paddingAndMarginH = sumStyleNumbers(filled, horizontalSpacing) + sumStyleNumbers(parent, horizontalSpacing);
    const availableHeight = parent.clientHeight - nav.clientHeight;

    filled.style.height = `${(availableHeight / 100) * height}px`;
    filled.style.width = `${contentSize(parent, 'width') - paddingAndMarginH}px`;
}

function resizePanels() {
    fillSpaceOnResize('layer-residues', 'right-tabs', 39);
    fillSpaceOnResize('layer-properties', 'right-tabs', 59);
    leftPanelTabs();
}

/** Places the tabs of the left panel next to it */
export function leftPanelTabs() {
    window.setTimeout(() => {
        const leftPanel = element('#left-panel');
        const tabs = element('#left-panel-tabs');
        if (leftPanel !== null && tabs !== null) {
            tabs.style.left = `${contentSize(leftPanel, 'width')}px`;
        }
    }, 10);
}

/** Gives the 3D view the height left by the sequence viewer and the bottom panel, and moves their togglers above them */
export function adjustCollapsiblePanels() {
    window.setTimeout(() => {
        const collapseSequence = element('#sequence-collapse');
        const collapseBottomPanel = element('#bottom-panel-collapse');
        const plugin = element('#plugin');
        const bottomPanel = element('#bottom-pannel');
        const sequenceViewer = element('#sequence-viewer');
        if (!collapseSequence || !collapseBottomPanel || !plugin || !bottomPanel || !sequenceViewer) {
            return;
        }

        const bottomShown = isShown(bottomPanel), sequenceShown = isShown(sequenceViewer);
        plugin.style.maxHeight = bottomShown
            ? (sequenceShown ? '49vh' : '66vh')
            : (sequenceShown ? '83vh' : '100vh');

        const bottomPanelHeight = bottomShown ? contentSize(bottomPanel, 'height') : 0;
        const sequenceViewerHeight = sequenceShown ? contentSize(sequenceViewer, 'height') : 21;

        collapseBottomPanel.style.bottom = `${bottomPanelHeight}px`;
        collapseSequence.style.bottom = `${sequenceViewerHeight + bottomPanelHeight}px`;
    }, 60);
}

/** Sets up the togglers and sizes; the listeners are on the document, as React renders the panels later */
export function initLayout() {
    // A shown tab gets its layout and the profile canvas, which skips resizing while hidden
    document.addEventListener('shown.bs.tab', e => {
        if ((e.target as Element).closest('#left-tabs a, #right-tabs a')) {
            resizePanels();
            emit('lvContentResize');
        }
    });

    document.addEventListener('click', e => {
        const target = e.target as Element;
        if (target.closest('#bottom-tabs-toggler')) {
            toggleClass('.left-tabs', 'bottom-tabs-toggled');
            toggleClass('.right-tabs', 'bottom-tabs-toggled');
            emitResize();
            emit('contentResize');
            emit('lvContentResize');
        } else if (target.closest('#left-panel-toggle-minimize')) {
            toggleClass('.left-panel', 'minimized');
            toggleClass('#left-panel-tabs a:not(#home-tab)', 'disabled');
            toggleClass('#left-panel .tab-content', 'hide');
            leftPanelTabs();
        }
    });

    for (const handler of [resizePanels, adjustCollapsiblePanels]) {
        window.addEventListener('resize', handler);
        listen('contentResize', handler);
    }
}
