import Tab from 'bootstrap/js/dist/tab';

export function getTabLinkById(tabbedElementId: string, tabId: string) {
    const href = `#${tabbedElementId}-${tabId}`;
    return [...document.querySelectorAll<HTMLElement>(`#${tabbedElementId} li a`)].find(a => a.getAttribute('href') === href);
}

export function isActive(tabbedElementId: string, tabId: string) {
    return getTabLinkById(tabbedElementId, tabId)?.classList.contains("active") ?? false;
}

export function activateTab(tabbedElementId: string, tabId: string) {
    let tabLink = getTabLinkById(tabbedElementId, tabId);
    if (tabLink) {
        Tab.getOrCreateInstance(tabLink).show();
    } else {
        console.error(`Tab with id "${tabId}" not found in element "${tabbedElementId}".`);
    }
}

// Calls the callback once the tab is shown; returns a function that cancels the call
export function doAfterTabActivated(tabbedElementId: string, tabId: string, callback: () => void): () => void {
    let timer: number;
    let checker = function () {
        let link = getTabLinkById(tabbedElementId, tabId);
        let pane = link && document.querySelector(link.getAttribute("href")!);
        if (link?.classList.contains("active") && pane && getComputedStyle(pane).display !== "none") {
            callback();
        }
        else {
            timer = window.setTimeout(checker, 10);
        }
    };
    timer = window.setTimeout(checker, 10);
    return () => window.clearTimeout(timer);
}
