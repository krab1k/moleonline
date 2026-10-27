// The pages without an application (about, news): Bootstrap's components and tooltips
import { initTooltips } from './Common/Bootstrap';

initTooltips();

// Addresses of this site to copy (the MCP server's), on the host the page is served from: dev, either production
// domain, localhost; the HTML has the production one
document.querySelectorAll<HTMLElement>('[data-site-url]').forEach(element => {
    element.textContent = location.origin + element.dataset.siteUrl;
});
