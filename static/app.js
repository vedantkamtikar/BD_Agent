document.addEventListener("DOMContentLoaded", () => {
    // --- DOM REFERENCES ---
    const form          = document.getElementById("run-form");
    const submitBtn     = document.getElementById("submit-btn");
    const btnText       = submitBtn.querySelector(".btn-text");
    const badge         = document.getElementById("status-badge");
    const badgeText     = badge.querySelector(".status-text");
    const threadCode    = document.getElementById("active-thread-id");
    const consoleLogs   = document.getElementById("console-logs");
    const logCountTag   = document.getElementById("log-count");
    const tbody         = document.getElementById("leads-tbody");
    const downloadBtn   = document.getElementById("download-btn");
    const countLabel    = document.getElementById("lead-count-label");

    // Progress & Summary Banner
    const progressWrap  = document.getElementById("progress-wrap");
    const progressFill  = document.getElementById("progress-fill");
    const progressLabel = document.getElementById("progress-label");
    const progressPct   = document.getElementById("progress-pct");
    const summaryBanner = document.getElementById("summary-banner");
    const summaryText   = document.getElementById("summary-banner-text");

    // Pipeline step elements
    const stepSearch       = document.getElementById("step-search");
    const stepSearchMeta   = document.getElementById("step-search-meta");
    const stepContacts     = document.getElementById("step-contacts");
    const stepContactsMeta = document.getElementById("step-contacts-meta");
    const stepDraft        = document.getElementById("step-draft");
    const stepDraftMeta    = document.getElementById("step-draft-meta");
    const allSteps         = [stepSearch, stepContacts, stepDraft];

    // Modal
    const modal        = document.getElementById("email-modal");
    const modalClose   = document.getElementById("modal-close-btn");
    const modalCancel  = document.getElementById("modal-cancel-btn");
    const modalTo      = document.getElementById("modal-email-to");
    const modalSubject = document.getElementById("modal-email-subject");
    const modalBody    = document.getElementById("modal-email-body");
    const copyBtn      = document.getElementById("copy-email-btn");
    const copyBtnText  = document.getElementById("copy-btn-text");

    const themeToggle  = document.getElementById("theme-toggle");
    const themeLabel   = document.getElementById("theme-label");

    let pollId = null;
    let logCount = 0;
    let currentLeads = [];

    // Initialize Theme
    initTheme();

    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
            const newTheme = currentTheme === "dark" ? "light" : "dark";
            setTheme(newTheme);
        });
    }

    function setTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem("theme-preference", theme);
        if (themeLabel) {
            themeLabel.textContent = theme.toUpperCase();
        }
    }

    function initTheme() {
        const savedTheme = localStorage.getItem("theme-preference");
        if (savedTheme) {
            setTheme(savedTheme);
        } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
            setTheme("light");
        } else {
            setTheme("dark");
        }
    }

    // Load initial lead records
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
        const draftEmailsEnabled = (document.getElementById("draft-emails-toggle")?.value || "true") === "true";
        const syncGmailDrafts    = (document.getElementById("sync-gmail-toggle")?.value || "true") === "true";

        if (!niche) return;

        setLoading(true);
        setBadge("running");
        clearConsole();
        resetSteps();
        resetProgress();
        if (summaryBanner) summaryBanner.classList.remove("show");
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
                    tone,
                    draft_emails_enabled: draftEmailsEnabled,
                    sync_gmail_drafts: syncGmailDrafts
                })
            });
            if (res.status === 409) {
                const errData = await res.json();
                throw new Error(errData.detail || "A pipeline is already running.");
            }
            if (!res.ok) throw new Error("Failed to initialize pipeline.");
            const data = await res.json();
            
            // Format shortened thread ID for clean operator UI display
            const shortId = data.thread_id ? data.thread_id.slice(0, 8).toUpperCase() : "ACTIVE";
            threadCode.textContent = `#${shortId}`;
            threadCode.title = `Full Thread ID: ${data.thread_id}`;

            startPolling(data.thread_id);
        } catch (err) {
            log(`[ERROR] ${err.message}`, "error");
            setBadge("failed");
            setLoading(false);
        }
    });

    // -------------------------------------------------------
    // STATUS POLLING
    // -------------------------------------------------------
    function startPolling(threadId) {
        if (pollId) clearInterval(pollId);

        pollId = setInterval(async () => {
            try {
                const res = await fetch(`/api/status/${threadId}`);
                if (!res.ok) throw new Error("Status query failed.");
                const data = await res.json();

                // Append logs
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
                    if (logCountTag) logCountTag.textContent = `${logCount} LINES`;
                }

                // Update step indicators & progress
                updateSteps(data.logs || [], data);

                if (data.progress) {
                    updateProgress(data.progress);
                }

                // Handle completion or failure
                if (data.status === "completed") {
                    clearInterval(pollId);
                    setBadge("completed");
                    setLoading(false);
                    doneAllSteps(data);
                    completeProgress(data);

                    if (data.lead_rows && data.lead_rows.length) {
                        renderTable(data.lead_rows);
                    } else {
                        loadLeads();
                    }
                } else if (data.status === "failed") {
                    clearInterval(pollId);
                    setBadge("failed");
                    setLoading(false);
                    log(`[FATAL] ${data.error || "Execution error"}`, "error");
                }
            } catch (err) {
                console.error("Poll error:", err);
            }
        }, 1000);
    }

    // -------------------------------------------------------
    // LEADS DATA TABLE RENDERER (STRICT ROW HEIGHT & LINKEDIN COLUMN)
    // -------------------------------------------------------
    async function loadLeads() {
        try {
            const res = await fetch("/api/leads");
            if (!res.ok) return;
            const data = await res.json();
            renderTable(data.leads || []);
        } catch (err) {
            console.error("loadLeads error:", err);
        }
    }

    function renderTable(leads) {
        currentLeads = leads || [];
        if (!currentLeads.length) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="table-empty-cell">
                        <div class="empty-state-box">
                            <span class="empty-code">NO DATA AVAILABLE</span>
                            <p class="empty-msg">Configure execution parameters in Panel 01 and trigger the pipeline to discover decision-makers.</p>
                        </div>
                    </td>
                </tr>`;
            downloadBtn.disabled = true;
            countLabel.textContent = "0 RECORDS";
            return;
        }

        downloadBtn.disabled = false;
        countLabel.textContent = `${currentLeads.length} RECORD${currentLeads.length !== 1 ? "S" : ""}`;
        tbody.innerHTML = "";

        [...currentLeads].reverse().forEach((lead) => {
            const tr = document.createElement("tr");

            const compName = esc(lead["Company Name"] || "N/A");
            const domain = lead["Company Domain"];
            const domainHtml = (domain && domain !== "N/A")
                ? `<a href="https://${domain}" target="_blank" rel="noopener" class="domain-link" title="${esc(domain)}">${esc(domain)}</a>`
                : `<span class="cell-sub">N/A</span>`;

            const contactName = esc(lead["Contact Name"] || "N/A");
            const contactTitle = esc(lead["Contact Title"] || "N/A");
            const rawEmail = lead["Contact Email"] || "";

            // LinkedIn URL rendering
            const rawLinkedin = lead["LinkedIn URL"] || "";
            const hasLinkedin = rawLinkedin && rawLinkedin !== "N/A" && rawLinkedin.includes("linkedin.com");
            const linkedinHtml = hasLinkedin
                ? `<a href="${esc(rawLinkedin)}" target="_blank" rel="noopener" class="domain-link" title="${esc(rawLinkedin)}">LinkedIn ↗</a>`
                : `<span class="badge-email-na">N/A</span>`;

            // Verified email signal badge
            const isVerifiedEmail = rawEmail && rawEmail !== "N/A" && rawEmail.includes("@");
            const emailHtml = isVerifiedEmail
                ? `<span class="badge-email-verified" title="${esc(rawEmail)}">${esc(rawEmail)}</span>`
                : `<span class="badge-email-na">N/A</span>`;

            const hasDraft = lead["Email Subject"]
                && !lead["Email Subject"].includes("skipped")
                && lead["Email Subject"] !== "N/A";

            tr.innerHTML = `
                <td title="${compName} (${domain || 'N/A'})">
                    <div class="cell-primary">${compName}</div>
                    <div class="cell-sub">${domainHtml}</div>
                </td>
                <td title="${esc(lead["Industry"] || "N/A")}">
                    <span class="industry-tag">${esc(lead["Industry"] || "N/A")}</span>
                </td>
                <td title="${contactName} - ${contactTitle}">
                    <div class="cell-primary">${contactName}</div>
                    <div class="cell-sub">${contactTitle}</div>
                </td>
                <td>${linkedinHtml}</td>
                <td>${emailHtml}</td>
                <td style="text-align: right;"></td>
            `;

            const actionTd = tr.querySelector("td:last-child");
            if (hasDraft) {
                const btn = document.createElement("button");
                btn.className = "btn-view-draft";
                btn.textContent = "Inspect ↗";
                btn.type = "button";
                btn.title = "Inspect generated cold outreach copy";
                btn.onclick = () => openModal(
                    lead["Contact Email"],
                    lead["Email Subject"],
                    lead["Email Body"]
                );
                actionTd.appendChild(btn);
            } else {
                actionTd.innerHTML = `<span class="text-skipped">SKIPPED</span>`;
            }

            tbody.appendChild(tr);
        });
    }

    // -------------------------------------------------------
    // DRAFT INSPECTOR MODAL
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

    [modalClose, modalCancel, modal].forEach(el => {
        el.addEventListener("click", (e) => {
            if (e.target === el) closeModal();
        });
    });

    copyBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(modalBody.textContent).then(() => {
            copyBtnText.textContent = "COPIED TO CLIPBOARD";
            setTimeout(() => { copyBtnText.textContent = "COPY DRAFT TEXT"; }, 1400);
        });
    });

    // CSV Export
    downloadBtn.addEventListener("click", () => {
        if (!currentLeads.length) return;
        const headers = ["Company Name", "Company Domain", "Industry", "Employees", "HQ", "Contact Name", "Contact Title", "LinkedIn URL", "Contact Email"];
        let csv = headers.join(",") + "\n";
        currentLeads.forEach(lead => {
            const vals = headers.map(h => {
                const text = String(lead[h] || "").replace(/"/g, '""');
                return `"${text}"`;
            });
            csv += vals.join(",") + "\n";
        });
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "leads_export.csv";
        a.click();
        URL.revokeObjectURL(url);
    });

    // Step tracker elements
    const stepGmail = document.getElementById("step-gmail");
    const stepGmailMeta = document.getElementById("step-gmail-meta");

    // -------------------------------------------------------
    // REFINEMENT 3: STEP TRACKER IN-PROGRESS & COMPLETED STATES
    // -------------------------------------------------------
    function resetSteps() {
        allSteps.forEach(s => { s.classList.remove("active", "done"); });
        stepSearchMeta.textContent = "Pending";
        stepContactsMeta.textContent = "Pending";
        stepDraftMeta.textContent = "Pending";
        if (stepGmailMeta) stepGmailMeta.textContent = "Pending";
    }

    function doneAllSteps(data) {
        allSteps.forEach(s => { s.classList.remove("active"); s.classList.add("done"); });
        
        const compCount = data && data.companies ? data.companies.length : 0;
        const contCount = data && data.contacts ? data.contacts.length : 0;
        const emailCount = data && data.emails ? data.emails.length : 0;

        stepSearchMeta.innerHTML = `<span class="step-check">✓</span> ${compCount} found`;
        stepContactsMeta.innerHTML = `<span class="step-check">✓</span> ${contCount} found`;
        stepDraftMeta.innerHTML = `<span class="step-check">✓</span> ${emailCount} drafted`;
        if (stepGmailMeta) stepGmailMeta.innerHTML = `<span class="step-check">✓</span> Synced`;

        if (summaryBanner) {
            summaryBanner.classList.add("show");
            if (summaryText) {
                summaryText.textContent = `${compCount} companies discovered • ${contCount} contacts identified • ${emailCount} outreach drafts synced to Gmail`;
            }
        }
    }

    function updateSteps(logs, data) {
        const joined = logs.join(" ");
        const search   = joined.includes("search_companies");
        const contacts = joined.includes("get_contacts");
        const draft    = joined.includes("draft_emails");
        const gmail    = joined.includes("create_gmail_drafts");

        const compCount = data && data.companies ? data.companies.length : 0;
        const contCount = data && data.contacts ? data.contacts.length : 0;
        const emailCount = data && data.emails ? data.emails.length : 0;

        if (gmail) {
            setStepDone(stepSearch, stepSearchMeta, `✓ ${compCount || ''} found`);
            setStepDone(stepContacts, stepContactsMeta, `✓ ${contCount || ''} found`);
            setStepDone(stepDraft, stepDraftMeta, `✓ ${emailCount || ''} drafted`);
            setStepActive(stepGmail, stepGmailMeta, "Syncing Gmail...");
        } else if (draft) {
            setStepDone(stepSearch, stepSearchMeta, `✓ ${compCount || ''} found`);
            setStepDone(stepContacts, stepContactsMeta, `✓ ${contCount || ''} found`);
            setStepActive(stepDraft, stepDraftMeta, "Drafting...");
        } else if (contacts) {
            setStepDone(stepSearch, stepSearchMeta, `✓ ${compCount || ''} found`);
            setStepActive(stepContacts, stepContactsMeta, "Searching...");
        } else if (search) {
            setStepActive(stepSearch, stepSearchMeta, "Running...");
        }
    }

    function setStepActive(step, metaEl, text) {
        if (!step.classList.contains("done")) {
            step.classList.add("active");
            if (metaEl) metaEl.textContent = text;
        }
    }

    function setStepDone(step, metaEl, text) {
        step.classList.remove("active");
        step.classList.add("done");
        if (metaEl) metaEl.innerHTML = `<span class="step-check">✓</span> ${text.replace('✓', '').trim()}`;
    }

    // -------------------------------------------------------
    // PROGRESS BAR HELPERS
    // -------------------------------------------------------
    function resetProgress() {
        progressWrap.classList.remove("completed");
        progressFill.style.width = "0%";
        progressLabel.textContent = "Initializing pipeline...";
        if (progressPct) progressPct.textContent = "0%";
    }

    function updateProgress(progress) {
        if (!progress) return;
        const pct = (typeof progress.percent === "number")
            ? progress.percent
            : (progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0);
            
        progressFill.style.width = pct + "%";
        if (progressPct) progressPct.textContent = pct + "%";
        if (progress.detail) progressLabel.textContent = progress.detail;
    }

    function completeProgress(data) {
        progressWrap.classList.add("completed");
        progressFill.style.width = "100%";
        if (progressPct) progressPct.textContent = "100%";
        progressLabel.textContent = "Pipeline execution complete ✓";
    }

    // -------------------------------------------------------
    // CONSOLE & LOG HELPERS
    // -------------------------------------------------------
    function clearConsole() {
        consoleLogs.innerHTML = "";
    }

    function log(text, cls = "") {
        const div = document.createElement("div");
        div.className = "terminal-line" + (cls ? ` ${cls}` : "");
        div.textContent = text;
        consoleLogs.appendChild(div);
        consoleLogs.scrollTop = consoleLogs.scrollHeight;
    }

    // -------------------------------------------------------
    // STATUS BADGE & BUTTON HELPERS
    // -------------------------------------------------------
    function setBadge(state) {
        badge.className = `status-indicator ${state}`;
        badgeText.textContent = state.toUpperCase();
    }

    function setLoading(on) {
        submitBtn.disabled = on;
        btnText.textContent = on ? "PIPELINE RUNNING..." : "EXECUTE PIPELINE";
    }

    function esc(s) {
        return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }
});
