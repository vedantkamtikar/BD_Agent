document.addEventListener("DOMContentLoaded", () => {
    // --- DOM References ---
    const form          = document.getElementById("run-form");
    const submitBtn     = document.getElementById("submit-btn");
    const btnLabel      = submitBtn.querySelector(".btn-label");
    const badge         = document.getElementById("status-badge");
    const badgeText     = badge.querySelector(".status-text");
    const threadCode    = document.getElementById("active-thread-id");
    const consoleLogs   = document.getElementById("console-logs");
    const tbody         = document.getElementById("leads-tbody");
    const downloadBtn   = document.getElementById("download-btn");
    const countLabel    = document.getElementById("lead-count-label");

    // Progress bar
    const progressWrap  = document.getElementById("progress-wrap");
    const progressFill  = document.getElementById("progress-fill");
    const progressLabel = document.getElementById("progress-label");

    // Pipeline steps
    const stepSearch   = document.getElementById("step-search");
    const stepContacts = document.getElementById("step-contacts");
    const stepDraft    = document.getElementById("step-draft");
    const allSteps     = [stepSearch, stepContacts, stepDraft];

    // Modal
    const modal        = document.getElementById("email-modal");
    const modalClose   = document.getElementById("modal-close-btn");
    const modalCancel  = document.getElementById("modal-cancel-btn");
    const modalBack    = document.getElementById("modal-backdrop");
    const modalTo      = document.getElementById("modal-email-to");
    const modalSubject = document.getElementById("modal-email-subject");
    const modalBody    = document.getElementById("modal-email-body");
    const copyBtn      = document.getElementById("copy-email-btn");

    const themeToggle  = document.getElementById("theme-toggle");

    let pollId = null;
    let logCount = 0;

    // Initialize Theme (Light / Dark)
    initTheme();

    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const currentTheme = document.documentElement.getAttribute("data-theme") || "light";
            const newTheme = currentTheme === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", newTheme);
            localStorage.setItem("theme-preference", newTheme);
        });
    }

    function initTheme() {
        const savedTheme = localStorage.getItem("theme-preference");
        if (savedTheme) {
            document.documentElement.setAttribute("data-theme", savedTheme);
        } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
            document.documentElement.setAttribute("data-theme", "dark");
        } else {
            document.documentElement.setAttribute("data-theme", "light");
        }
    }

    // Load existing leads on init
    loadLeads();

    // -------------------------------------------------------
    // FORM SUBMISSION
    // -------------------------------------------------------
    form.addEventListener("submit", async (e) => {
        e.preventDefault();

        const niche      = document.getElementById("niche").value.trim();
        const location   = document.getElementById("location").value.trim();
        const limit      = parseInt(document.getElementById("limit").value, 10);
        const minRevenue = (document.getElementById("min-revenue")?.value || "").trim();
        const maxRevenue = (document.getElementById("max-revenue")?.value || "").trim();
        const senderName = document.getElementById("sender-name").value.trim();
        const senderTitle = document.getElementById("sender-title").value.trim();
        const tone       = document.getElementById("outreach-tone").value;
        if (!niche) return;

        setLoading(true);
        setBadge("running");
        clearConsole();
        resetSteps();
        resetProgress();
        logCount = 0;

        try {
            const res = await fetch("/api/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    niche,
                    location,
                    limit,
                    min_revenue: minRevenue,
                    max_revenue: maxRevenue,
                    sender_name: senderName,
                    sender_title: senderTitle,
                    tone
                })
            });
            if (!res.ok) throw new Error("Failed to start pipeline.");
            const data = await res.json();
            threadCode.textContent = data.thread_id;
            startPolling(data.thread_id);
        } catch (err) {
            log(err.message, "error");
            setBadge("failed");
            setLoading(false);
        }
    });

    // -------------------------------------------------------
    // POLLING
    // -------------------------------------------------------
    function startPolling(threadId) {
        if (pollId) clearInterval(pollId);

        pollId = setInterval(async () => {
            try {
                const res = await fetch(`/api/status/${threadId}`);
                if (!res.ok) throw new Error("Poll failed.");
                const data = await res.json();

                // Append new log lines
                if (data.logs && data.logs.length > logCount) {
                    for (let i = logCount; i < data.logs.length; i++) {
                        const line = data.logs[i];
                        let cls = "";
                        if (line.includes("Node:") || line.includes("ENTERING:")) cls = "node";
                        else if (line.includes("Success") || line.includes("successfully") || line.includes("completed")) cls = "success";
                        else if (line.includes("Error") || line.includes("failed") || line.includes("Fatal")) cls = "error";
                        log(line, cls);
                    }
                    logCount = data.logs.length;
                }

                // Pipeline step visuals
                updateSteps(data.logs || []);

                // Progress bar update
                if (data.progress) {
                    updateProgress(data.progress);
                }

                // Terminal states
                if (data.status === "completed") {
                    clearInterval(pollId);
                    setBadge("completed");
                    setLoading(false);
                    doneAllSteps();
                    completeProgress();
                    // Load leads from the completed run
                    if (data.lead_rows && data.lead_rows.length) {
                        renderTable(data.lead_rows);
                    } else {
                        loadLeads();
                    }
                } else if (data.status === "failed") {
                    clearInterval(pollId);
                    setBadge("failed");
                    setLoading(false);
                    log(`Failure: ${data.error || "Unknown error"}`, "error");
                }
            } catch (err) {
                console.error("Poll error:", err);
            }
        }, 1000);
    }

    // -------------------------------------------------------
    // LEADS TABLE
    // -------------------------------------------------------
    async function loadLeads() {
        try {
            const res = await fetch("/api/leads");
            if (!res.ok) return;
            const data = await res.json();
            renderTable(data.leads || []);
        } catch (err) {
            console.error("loadLeads:", err);
        }
    }

    function renderTable(leads) {
        if (!leads.length) {
            tbody.innerHTML = `<tr><td colspan="11" class="empty-state">
                <span class="empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg></span>
                No records. Execute the pipeline to populate.</td></tr>`;
            downloadBtn.disabled = true;
            countLabel.textContent = "No records";
            return;
        }

        downloadBtn.disabled = false;
        countLabel.textContent = `${leads.length} lead${leads.length !== 1 ? "s" : ""} recorded`;
        tbody.innerHTML = "";

        [...leads].reverse().forEach((lead, i) => {
            const tr = document.createElement("tr");
            tr.style.animationDelay = `${i * 30}ms`;

            const domain = lead["Company Domain"];
            const domainHtml = domain && domain !== "N/A"
                ? `<a href="https://${domain}" target="_blank" rel="noopener" class="table-link">${domain}</a>`
                : `<span class="text-muted">&mdash;</span>`;

            const hasEmail = lead["Email Subject"]
                && !lead["Email Subject"].includes("skipped")
                && lead["Email Subject"] !== "N/A";

            tr.innerHTML = `
                <td><strong>${esc(lead["Company Name"] || "")}</strong></td>
                <td>${domainHtml}</td>
                <td>${esc(lead["Industry"] || "")}</td>
                <td title="${esc(lead["Company Description"] || "")}">${truncate(esc(lead["Company Description"] || ""), 50)}</td>
                <td>${esc(lead["Employees"] || "N/A")}</td>
                <td>${esc(lead["Founded"] || "N/A")}</td>
                <td>${esc(lead["HQ"] || "N/A")}</td>
                <td>${esc(lead["Contact Name"] || "")}</td>
                <td>${esc(lead["Contact Title"] || "")}</td>
                <td>${esc(lead["Contact Email"] || "")}</td>
                <td></td>
            `;

            const actionTd = tr.querySelector("td:last-child");
            if (hasEmail) {
                const btn = document.createElement("button");
                btn.className = "view-btn";
                btn.textContent = "View";
                btn.onclick = () => openModal(
                    lead["Contact Email"],
                    lead["Email Subject"],
                    lead["Email Body"]
                );
                actionTd.appendChild(btn);
            } else {
                actionTd.innerHTML = `<span class="text-muted">Skipped</span>`;
            }

            tbody.appendChild(tr);
        });
    }

    // -------------------------------------------------------
    // MODAL
    // -------------------------------------------------------
    function openModal(to, subject, body) {
        modalTo.textContent      = to || "N/A";
        modalSubject.textContent = subject || "No Subject";
        modalBody.textContent    = body || "";
        modal.classList.add("show");
    }

    function closeModal() {
        modal.classList.remove("show");
    }

    [modalClose, modalCancel, modalBack].forEach(el => {
        el.addEventListener("click", closeModal);
    });

    copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(modalBody.textContent).then(() => {
            const orig = copyBtn.querySelector(".btn-label")?.textContent || copyBtn.textContent;
            copyBtn.textContent = "Copied";
            setTimeout(() => { copyBtn.textContent = orig; }, 1400);
        });
    });

    downloadBtn.addEventListener("click", () => {
        // Export current table data as CSV client-side
        const rows = document.querySelectorAll("#leads-tbody tr");
        if (!rows.length) return;
        const headers = ["Company Name", "Company Domain", "Industry", "Company Description", "Employees", "Founded", "HQ", "Contact Name", "Contact Title", "Contact Email"];
        let csv = headers.join(",") + "\n";
        rows.forEach(tr => {
            const cells = tr.querySelectorAll("td");
            if (cells.length < 10) return;
            const vals = Array.from(cells).slice(0, 10).map(td => {
                const text = td.textContent.trim().replace(/"/g, '""');
                return `"${text}"`;
            });
            csv += vals.join(",") + "\n";
        });
        const blob = new Blob([csv], { type: "text/csv" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "leads_export.csv";
        a.click();
        URL.revokeObjectURL(url);
    });

    // -------------------------------------------------------
    // PIPELINE STEP HELPERS
    // -------------------------------------------------------
    function resetSteps() {
        allSteps.forEach(s => { s.classList.remove("active", "done"); });
    }

    function doneAllSteps() {
        allSteps.forEach(s => { s.classList.remove("active"); s.classList.add("done"); });
    }

    function updateSteps(logs) {
        const joined = logs.join(" ");
        const search   = joined.includes("search_companies");
        const contacts = joined.includes("get_contacts");
        const draft    = joined.includes("draft_emails");

        if (draft) {
            stepSearch.classList.replace("active", "done")   || stepSearch.classList.add("done");
            stepContacts.classList.replace("active", "done") || stepContacts.classList.add("done");
            activate(stepDraft);
        } else if (contacts) {
            stepSearch.classList.replace("active", "done")   || stepSearch.classList.add("done");
            activate(stepContacts);
        } else if (search) {
            activate(stepSearch);
        }
    }

    function activate(step) {
        if (!step.classList.contains("done")) step.classList.add("active");
    }

    // -------------------------------------------------------
    // PROGRESS BAR HELPERS
    // -------------------------------------------------------
    function resetProgress() {
        progressWrap.style.display = "flex";
        progressFill.style.width = "0%";
        progressLabel.textContent = "Initializing...";
    }

    function updateProgress(progress) {
        if (!progress) return;
        const pct = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;
        progressFill.style.width = pct + "%";
        progressLabel.textContent = progress.detail || `${progress.current}/${progress.total}`;
    }

    function completeProgress() {
        progressFill.style.width = "100%";
        progressLabel.textContent = "Pipeline complete ✓";
    }

    // -------------------------------------------------------
    // CONSOLE HELPERS
    // -------------------------------------------------------
    function clearConsole() {
        consoleLogs.innerHTML = "";
    }

    function log(text, cls = "") {
        const p = document.createElement("p");
        p.className = "console-line" + (cls ? ` ${cls}` : "");
        p.textContent = text;
        consoleLogs.appendChild(p);
        consoleLogs.scrollTop = consoleLogs.scrollHeight;
    }

    // -------------------------------------------------------
    // BADGE & BUTTON HELPERS
    // -------------------------------------------------------
    function setBadge(state) {
        badge.className = `status-badge ${state}`;
        badgeText.textContent = state.charAt(0).toUpperCase() + state.slice(1);
    }

    function setLoading(on) {
        submitBtn.disabled = on;
        submitBtn.classList.toggle("loading", on);
        btnLabel.textContent = on ? "Running..." : "Execute Pipeline";
    }

    // -------------------------------------------------------
    // UTILITIES
    // -------------------------------------------------------
    function truncate(s, n) {
        return s && s.length > n ? s.slice(0, n) + "..." : s;
    }

    function esc(s) {
        return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
});
