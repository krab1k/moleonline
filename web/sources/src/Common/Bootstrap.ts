// Bootstrap's JavaScript components that the pages use; collapses, dropdowns and tabs work through their data-bs-toggle
// attributes once imported
import 'bootstrap/js/dist/collapse';
import 'bootstrap/js/dist/dropdown';
import 'bootstrap/js/dist/tab';
import Tooltip from 'bootstrap/js/dist/tooltip';

// Tooltips of the elements with data-bs-toggle="tooltip", also those rendered later (delegated from the body)
export function initTooltips() {
    new Tooltip(document.body, { selector: '[data-bs-toggle="tooltip"]' });
}
