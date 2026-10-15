Here is a detailed audit of the UI/UX and template implementation across the project, identifying bugs, UX friction points, layout inconsistencies, and dead code.

🚨 1. High-Impact Bugs & Incomplete Features
A. Incomplete "Dark Mode" Implementation (Theme Toggle Illusion)
Location: schoolsystem/templates/base.html, schoolsystem/templates/components/header_user_menu.html, tailwind.config.js
Issue: Commit bb18aab implemented theme mode switching logic (light, dark, system) and added an "Appearance" dropdown in the header user menu. However, not a single template contains any Tailwind dark: variant classes (0 occurrences across the entire templates/ folder).
Impact: When a user selects "Dark", the .dark class is toggled on <html>, but the interface remains completely white/light-slate. To users, this looks like a broken button.
Fix: Either flesh out the dark mode classes (dark:bg-slate-900, dark:text-white, etc.) across main views, or temporarily remove the theme dropdown until the dark palette is authored.
B. Misleading Global Error Message on HTMX Failures
Location: templates/components/toasts.html
Code:
javascript
document.body.addEventListener("htmx:responseError", () => {
  document.body.dispatchEvent(new CustomEvent("showToast", {
    detail: {
      level: "error",
      message: "The attendance update failed. Please try again.",
    },
  }));
});
Issue: This global listener catches any failed HTMX request across the entire system.
Impact: If a network drop or 500 error occurs while searching students in the Registry, filtering the Dashboard Exeat table, or running Global Search, the toast pops up saying: "The attendance update failed. Please try again."
Fix: Change the generic message to "An error occurred while communicating with the server. Please try again.", or allow the failing element to specify a custom message via an attribute (e.g. data-error-message).
⚠️ 2. UX Friction & Interactive Gaps
C. Student Registration Lacks Dependent Cascading Dropdowns
Location: templates/core/students/registration_form.html & core/forms.py
Issue: When creating or editing a student, the Dormitory dropdown lists all dormitories from all houses, and the Room dropdown lists all rooms across the school.
Impact: If a user selects "House 1 (Red)", they can still accidentally select a dormitory belonging to "House 2 (Blue)". The user only discovers the mistake after submitting the form and receiving a backend ValidationError.
Fix: Add HTMX triggers (hx-get to an options endpoint on change of House and Dormitory) or a client-side Alpine filter so picking a House automatically narrows the Dormitory and Room options.
D. Dropdown Menu Inconsistency Between Tables (overflow-x-auto Clipping)
Location: templates/core/conduct/partials/conduct_table.html vs templates/core/exeats/partials/exeat_table.html
Issue: Commit 0c46ce6 fixed dropdown clipping in exeat_table.html by computing fixed viewport coordinates with JS (position: fixed). However, conduct_table.html was not updated and still uses position: absolute inside <div class="pb-16 overflow-x-auto">.
Impact: In the Disciplinary Register table, on smaller desktop screens or when scrolled horizontally, the action menu gets clipped or forces horizontal scroll expansion.
Fix: Align conduct_table.html with the positioning pattern used in exeat_table.html, and add @scroll.window="menuOpen = false" so open fixed menus close cleanly when the page or table is scrolled.
E. Countdown Timer Leak on Roll Call Roster HTMX Swaps
Location: templates/core/operations/partials/roll_call_roster.html
Issue: Inside the swapped HTMX container #roll-call-roster, an inline script starts window.setInterval(updateCountdown, 1000).
Impact: Every time a user changes pages or updates entries via HTMX, a new 1-second interval is spawned without clearing previous intervals.
Fix: Move the timer into an Alpine component (x-data) with an init() and destroy() lifecycle or clear window._rollCallTimer before re-attaching.
F. Global Search Fires Unnecessary Network Requests on Single Characters
Location: templates/components/global_search.html
Issue: Alpine only opens the search flyout when query.trim().length >= 2. However, the <input> has hx-trigger="input changed delay:200ms".
Impact: Typing a single letter (e.g., "A") sends an HTMX request to /search/?q=a. The backend returns an empty results partial that is immediately discarded and never seen.
Fix: Guard the HTMX trigger or Alpine input event (e.g. hx-trigger="input changed delay:250ms[this.value.trim().length >= 2], search").
🎨 3. Visual & Layout Inconsistencies
G. Row Grid Alignment Jump on "Absent" in Roll Call
Location: templates/core/operations/partials/entry_row.html
Issue: Normal rows use sm:col-span-7 and sm:col-span-5 to align with the table headers ("Student / Dorm / Class" vs "Attendance Status"). When marked Absent, it drops the column spans to col-span-12.
Impact: Selecting "Absent" causes the row to jump into a vertically stacked two-line layout while all other rows remain side-by-side, breaking the visual rhythm of the table.
H. Missing [x-cloak] CSS in login.html (FOUC)
Location: templates/registration/login.html
Issue: base.html defines [x-cloak] { display: none !important; }, but login.html is an independent template that does not inherit from base.html. It uses Alpine and x-cloak on the password eye toggle (line 100), but lacks the [x-cloak] CSS definition.
Impact: On slow connections or cold cache loads, the eye icon and elements with x-cloak can flash unstyled before Alpine initializes.
Fix: Add [x-cloak] { display: none !important; } to <style> in login.html.
🧹 4. Dead Code & Template Cleanup
I. Orphaned Prototype Files
Files:
templates/base1.html
templates/core/index1.html
Finding: These are unreferenced mockups/prototypes from early development. They are not called by any view, view test, or {% extends %} tag.
Fix: Safe to delete.
Recommended Action Plan
Bug Fixes:
Update toasts.html error handler to show a contextual or general message instead of hardcoding "attendance update failed".
Add [x-cloak] { display: none !important; } and aria-label to login.html.
UX Enhancements:
Either hide or properly style the dark mode toggle.
Add @scroll.window="menuOpen = false" to table action dropdowns in exeat_table.html and conduct_table.html.
Prevent HTMX search triggers for queries under 2 characters in global_search.html.
Cleanup:
Remove unused base1.html and index1.html.