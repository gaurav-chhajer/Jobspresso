"use strict";
/* Progressive enhancement for Admin > Companies: filter chips, sort, live count.
   Without JavaScript the full list still renders and every action still works. */
(() => {
    const controls = document.getElementById("co-controls");
    const body = document.querySelector("#co-table tbody");
    if (!controls || !body) return;

    const rows = Array.from(body.querySelectorAll(".co-row"));
    const chips = Array.from(controls.querySelectorAll("[data-filter]"));
    const sort = document.getElementById("co-sort");
    const status = document.getElementById("co-status");
    let filter = "all";
    const paged = controls.dataset.paged === "1";

    const tests = {
        all: () => true,
        attention: r => r.dataset.attention === "1",
        active: r => r.dataset.active === "1",
        inactive: r => r.dataset.active === "0",
        discovery: r => r.dataset.discovery === "1",
    };
    const sorters = {
        name: (a, b) => a.dataset.name.localeCompare(b.dataset.name),
        attention: (a, b) => a.dataset.rank - b.dataset.rank || a.dataset.name.localeCompare(b.dataset.name),
        subscribers: (a, b) => b.dataset.subscribers - a.dataset.subscribers || a.dataset.name.localeCompare(b.dataset.name),
        jobs: (a, b) => b.dataset.jobs - a.dataset.jobs || a.dataset.name.localeCompare(b.dataset.name),
    };

    function update() {
        rows.sort(sorters[sort.value]).forEach(r => body.appendChild(r));
        let shown = 0;
        for (const r of rows) {
            const ok = tests[filter](r);
            r.hidden = !ok;
            if (ok) shown++;
        }
        chips.forEach(c => c.setAttribute("aria-pressed", String(c.dataset.filter === filter)));
        status.textContent = shown
            ? "Showing " + shown + " of " + rows.length + (paged ? " companies on this page. Filters and counts cover this page only." : " companies.")
            : "No companies match this filter.";
    }

    chips.forEach(c => c.addEventListener("click", () => { filter = c.dataset.filter; update(); }));
    sort.addEventListener("change", update);
    update();
})();
