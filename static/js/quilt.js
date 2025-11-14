(function () {
    const widget = document.getElementById("quilt-widget");
    if (!widget) {
        return;
    }

    const toggle = widget.querySelector("#quilt-toggle");
    const panel = widget.querySelector("#quilt-panel");
    const status = widget.querySelector(".quilt-status");
    const kpiGrid = widget.querySelector(".quilt-kpi-grid");
    const weldList = widget.querySelector(".quilt-weld-list");
    const showMoreBtn = widget.querySelector(".quilt-show-more");
    const truncationText = widget.querySelector(".quilt-truncation");
    const filtersForm = widget.querySelector("#quilt-filters");
    const projectSelect = widget.querySelector("#quilt-project-select");
    const quickActions = widget.querySelectorAll(".quilt-action");

    const orgId = widget.dataset.orgId || null;
    const preselectedProjectId = widget.dataset.projectId || null;
    const defaultPerPage = 25;
    const maxPerPage = 50;
    const exportMax = 100;

    let currentPage = 1;
    let lastPayload = null;
    let isLoading = false;

    function getCookie(name) {
        const cookieValue = document.cookie
            .split(";")
            .map((c) => c.trim())
            .find((c) => c.startsWith(name + "="));
        return cookieValue ? decodeURIComponent(cookieValue.split("=")[1]) : null;
    }

    function togglePanel(forceOpen) {
        const shouldOpen = forceOpen !== undefined ? forceOpen : panel.hasAttribute("hidden");
        if (shouldOpen) {
            panel.removeAttribute("hidden");
            toggle.setAttribute("aria-expanded", "true");
            filtersForm.querySelector("select, input")?.focus({ preventScroll: true });
        } else {
            panel.setAttribute("hidden", "");
            toggle.setAttribute("aria-expanded", "false");
        }
    }

    function setStatus(message, isError) {
        status.textContent = message || "";
        status.classList.toggle("quilt-status-error", Boolean(isError));
    }

    function formatNumber(value) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value);
    }

    function buildPayload(overrides) {
        const formData = new FormData(filtersForm);
        const selectedProject = projectSelect.value || preselectedProjectId || "";
        const payload = {
            intent: "repairs_summary",
            per_page: defaultPerPage,
            page: currentPage,
            filters: {},
        };

        if (orgId) {
            payload.org_id = parseInt(orgId, 10);
        }
        if (selectedProject) {
            payload.project_id = parseInt(selectedProject, 10);
        }

        const startDate = formData.get("start_date");
        const endDate = formData.get("end_date");
        const welder = formData.get("welder");
        if (startDate) {
            payload.filters.start_date = startDate;
        }
        if (endDate) {
            payload.filters.end_date = endDate;
        }
        if (welder) {
            payload.filters.welder_id = welder;
        }

        return Object.assign(payload, overrides || {});
    }

    function renderKpi(kpi) {
        kpiGrid.innerHTML = "";
        if (!kpi) {
            return;
        }
        const cards = [
            { label: "Repair rate", value: `${formatNumber((kpi.repair_rate || 0) * 100)}%` },
            { label: "Repaired welds", value: formatNumber(kpi.repaired_welds || 0) },
            { label: "Total welds", value: formatNumber(kpi.total_welds || 0) },
        ];
        if (Array.isArray(kpi.top_welders) && kpi.top_welders.length) {
            const topWelder = kpi.top_welders[0];
            cards.push({
                label: "Top welder",
                value: `${topWelder.name || "–"} (${formatNumber(topWelder.repairs || 0)})`,
            });
        }
        for (const card of cards) {
            const wrapper = document.createElement("div");
            wrapper.className = "quilt-kpi-card";
            wrapper.setAttribute("role", "listitem");
            wrapper.innerHTML = `<h3>${card.label}</h3><p>${card.value}</p>`;
            kpiGrid.appendChild(wrapper);
        }
    }

    function renderWelds(welds, append) {
        if (!append) {
            weldList.innerHTML = "";
        }
        welds.forEach((weld) => {
            const item = document.createElement("li");
            item.className = "quilt-weld-card";
            item.innerHTML = `
                <h4>${weld.identifier || `Weld #${weld.id}`}</h4>
                <p>Status: ${weld.status || "–"}</p>
                <p>Repairs: ${formatNumber(weld.repairs_count || 0)}</p>
                ${weld.last_repair_date ? `<p>Last repair: ${weld.last_repair_date}</p>` : ""}
                ${weld.weld_url ? `<p><a href="${weld.weld_url}">Open weld</a></p>` : ""}
            `;
            weldList.appendChild(item);
        });
    }

    function renderTruncation(meta) {
        const displayed = weldList.children.length;
        if (!meta || meta.total <= displayed) {
            truncationText.setAttribute("hidden", "");
            showMoreBtn.setAttribute("hidden", "");
            return;
        }
        truncationText.textContent = `Showing ${displayed} of ${meta.total}.`;
        truncationText.removeAttribute("hidden");
        showMoreBtn.removeAttribute("hidden");
    }

    function renderResponse(payload, data, append) {
        if (typeof data.answer === "string") {
            setStatus(data.answer, false);
        } else {
            setStatus("", false);
        }
        renderKpi(data.kpi);
        renderWelds(data.welds || [], append);
        renderTruncation(data.meta);
        if (data.meta && data.meta.export_limited) {
            status.textContent = `${status.textContent} Export limited to 100 rows. Use Background Export for larger datasets.`.trim();
        }
        if (Array.isArray(data.meta?.notes)) {
            const notes = data.meta.notes.join(" ");
            if (notes) {
                status.textContent = `${status.textContent} ${notes}`.trim();
            }
        }
        lastPayload = payload;
    }

    function handleErrorResponse(response, body) {
        const message = body?.error || body?.detail || `Request failed (${response.status}).`;
        setStatus(message, true);
    }

    async function runQuery(payload, { append = false } = {}) {
        if (isLoading) {
            return;
        }
        if (!payload.project_id && !payload.org_id) {
            setStatus("Select a project to run QUILT queries.", true);
            return;
        }
        if (!payload.project_id && !orgId) {
            setStatus("Select a project to run QUILT queries.", true);
            return;
        }
        isLoading = true;
        setStatus("Loading…", false);
        const csrftoken = getCookie("csrftoken");
        try {
            const response = await fetch("/api/quilt/query/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrftoken || "",
                },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                handleErrorResponse(response, data);
                return;
            }
            currentPage = payload.page || 1;
            renderResponse(payload, data, append);
        } catch (error) {
            console.error("Failed to run QUILT query", error);
            setStatus("Unable to reach QUILT right now. Please try again shortly.", true);
        } finally {
            isLoading = false;
        }
    }

    async function loadSources() {
        try {
            const response = await fetch("/api/quilt/sources/");
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            const orgs = Array.isArray(data.organizations) ? data.organizations : [];
            projectSelect.innerHTML = "";
            if (!preselectedProjectId) {
                const placeholder = document.createElement("option");
                placeholder.value = "";
                placeholder.textContent = "Select a project";
                projectSelect.appendChild(placeholder);
            }
            orgs.forEach((org) => {
                const group = document.createElement("optgroup");
                group.label = org.name;
                (org.projects || []).forEach((project) => {
                    const option = document.createElement("option");
                    option.value = project.id;
                    option.textContent = project.name;
                    if (String(project.id) === preselectedProjectId) {
                        option.selected = true;
                    }
                    group.appendChild(option);
                });
                projectSelect.appendChild(group);
            });
            if (preselectedProjectId) {
                projectSelect.value = preselectedProjectId;
                projectSelect.setAttribute("aria-disabled", "true");
                projectSelect.disabled = true;
            }
        } catch (error) {
            console.error("Unable to load QUILT sources", error);
        }
    }

    toggle.addEventListener("click", () => togglePanel());
    toggle.addEventListener("keydown", (event) => {
        if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            togglePanel();
        }
    });

    filtersForm.addEventListener("submit", (event) => {
        event.preventDefault();
        currentPage = 1;
        const payload = buildPayload();
        payload.intent = "list_welds";
        runQuery(payload);
    });

    showMoreBtn.addEventListener("click", () => {
        if (!lastPayload) {
            return;
        }
        const nextPage = (lastPayload.page || 1) + 1;
        const payload = Object.assign({}, lastPayload, { page: nextPage });
        payload.per_page = Math.min(payload.per_page || defaultPerPage, payload.intent === "export_csv" ? exportMax : maxPerPage);
        runQuery(payload, { append: true });
    });

    quickActions.forEach((button) => {
        button.addEventListener("click", () => {
            currentPage = 1;
            const action = button.dataset.action;
            const payload = buildPayload({ intent: action });
            if (action === "export_csv") {
                payload.per_page = exportMax;
            } else if (action === "top_welders" || action === "repair_rate") {
                payload.intent = "kpi";
            }
            payload.per_page = Math.min(payload.per_page || defaultPerPage, action === "export_csv" ? exportMax : maxPerPage);
            runQuery(payload);
        });
    });

    // Initial guidance message if no project is preselected.
    if (!preselectedProjectId && !orgId) {
        setStatus("Select a project to run QUILT queries.", false);
    }

    loadSources();
})();
