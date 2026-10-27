export function setCookie(c_name: string, value: string, exdays?: number) {
    let exdate = new Date();
    if (exdays !== void 0) {
        exdate.setDate(exdate.getDate() + exdays);
    }
    let c_value = encodeURI(value) + ((exdays === void 0) ? "" : "; expires=" + exdate.toUTCString()) + "; path=/";
    document.cookie = c_name + "=" + c_value;
}

export function getCookie(c_name: string) {
    for (const cookie of document.cookie.split(";")) {
        const eq = cookie.indexOf("="); // the value can contain "=" too
        if (eq !== -1 && cookie.slice(0, eq).trim() === c_name) {
            return decodeURI(cookie.slice(eq + 1));
        }
    }
    return undefined;
}