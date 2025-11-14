(function () {
  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  function getCsrfToken() {
    const name = "csrftoken";
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (let i = 0; i < cookies.length; i += 1) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith(name + "=")) {
        return decodeURIComponent(cookie.slice(name.length + 1));
      }
    }
    return "";
  }

  function formatPercent(value) {
    if (typeof value !== "number") {
      return "0%";
    }
    return (value * 100).toFixed(1) + "%";
  }

  function formatDate(value) {
    if (!value) {
      return "";
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleDateString();
  }

  const quickPrompts = {
    repairs_summary: {
      intent: "repairs_summary",
      query: "Summarize weld repairs",
    },
    kpi: {
      intent: "kpi",
      query: "Show repair KPIs",
    },
    repair_rate: {
      intent: "kpi",
      query: "What is the repair rate?",
    },
    export_csv: {
      intent: "export_csv",
      query: "Export repaired welds",
      per_page: 100,
    },
  };

  ready(function () {
    const root = document.querySelector("[data-quilt-root]");
    if (!root) {
      return;
    }
    const toggleButton = root.querySelector(".quilt-toggle");
    const closeButton = root.querySelector(".quilt-close");
    const panel = root.querySelector(".quilt-panel");
    const form = root.querySelector(".quilt-form");
    const input = form.querySelector("input[name='query']");
    const answerEl = root.querySelector(".quilt-answer");
    const kpisEl = root.querySelector(".quilt-kpis");
    const weldsEl = root.querySelector(".quilt-welds");
    const paginationEl = root.querySelector(".quilt-pagination");
    const showMoreBtn = root.querySelector(".quilt-show-more");
    const quickButtons = root.querySelectorAll(".quilt-quick");

    root.hidden = false;

    const state = {
      perPage: 25,
      total: 0,
      shown: 0,
      lastPayload: null,
    };

    function scopePayload() {
      const payload = {};
      const body = document.body;
      if (!body) {
        return payload;
      }
      const { dataset } = body;
      if (dataset.projectId) {
        const projectId = parseInt(dataset.projectId, 10);
        if (!Number.isNaN(projectId)) {
          payload.project_id = projectId;
        }
      }
      if (dataset.orgId) {
        const orgId = parseInt(dataset.orgId, 10);
        if (!Number.isNaN(orgId)) {
          payload.org_id = orgId;
        }
      }
      return payload;
    }

    function toggle(open) {
      if (open) {
        root.classList.add("is-open");
        toggleButton.setAttribute("aria-expanded", "true");
        setTimeout(function () {
          input.focus();
        }, 80);
      } else {
        root.classList.remove("is-open");
        toggleButton.setAttribute("aria-expanded", "false");
      }
    }

    function resetResults() {
      answerEl.textContent = "";
      kpisEl.innerHTML = "";
      weldsEl.innerHTML = "";
      paginationEl.textContent = "";
      showMoreBtn.hidden = true;
    }

    function renderKpi(kpi) {
      if (!kpi || typeof kpi.total_welds === "undefined") {
        kpisEl.innerHTML = "";
        return;
      }
      const topWelders = Array.isArray(kpi.top_welders) ? kpi.top_welders : [];
      const listItems = topWelders
        .map(function (welder) {
          const name = welder.name || "Unknown";
          const repairs = typeof welder.repairs === "number" ? welder.repairs : 0;
          const profile = welder.profile_url ? `<a href="${welder.profile_url}" class="text-decoration-none" target="_blank" rel="noopener">${name}</a>` : name;
          return `<li class="d-flex justify-content-between"><span>${profile}</span><span class="text-muted">${repairs}</span></li>`;
        })
        .join("");
      kpisEl.innerHTML = [
        '<div class="card quilt-kpi-card p-3">',
        `  <div class="d-flex justify-content-between align-items-center mb-2">
            <div>
              <div class="text-muted small">Repair rate</div>
              <div class="fs-5 fw-semibold">${formatPercent(kpi.repair_rate)}</div>
            </div>
            <div class="text-end small text-muted">
              <div>Total welds: <strong>${kpi.total_welds}</strong></div>
              <div>Repairs: <strong>${kpi.repaired_welds}</strong></div>
            </div>
          </div>`,
        topWelders.length
          ? `  <div>
                <div class="text-muted text-uppercase small fw-semibold mb-1">Top welders</div>
                <ul class="list-unstyled mb-0 small">${listItems}</ul>
              </div>`
          : "",
        "</div>",
      ].join("");
    }

    function ensureWeldList() {
      let card = weldsEl.querySelector(".quilt-weld-card");
      if (!card) {
        card = document.createElement("div");
        card.className = "card quilt-weld-card p-3";
        const list = document.createElement("ul");
        list.className = "quilt-weld-list";
        card.appendChild(list);
        weldsEl.appendChild(card);
      }
      return card.querySelector(".quilt-weld-list");
    }

    function renderWelds(welds, append) {
      if (!Array.isArray(welds) || welds.length === 0) {
        if (!append) {
          weldsEl.innerHTML = "";
        }
        return;
      }
      const list = ensureWeldList();
      if (!append) {
        list.innerHTML = "";
      }
      welds.forEach(function (weld) {
        const li = document.createElement("li");
        const identifier = weld.identifier || `Weld #${weld.id}`;
        const weldLink = weld.weld_url ? `<a href="${weld.weld_url}" class="btn btn-link btn-sm text-decoration-none" target="_blank" rel="noopener">View weld</a>` : "";
        const welder = weld.welder && weld.welder.name ? weld.welder.name : "";
        const welderLink = weld.welder && weld.welder.profile_url
          ? `<a class="text-decoration-none" href="${weld.welder.profile_url}" target="_blank" rel="noopener">${welder}</a>`
          : welder;
        const metaBits = [];
        if (typeof weld.repairs_count === "number") {
          metaBits.push(`${weld.repairs_count} repair${weld.repairs_count === 1 ? "" : "s"}`);
        }
        if (weld.last_repair_date) {
          metaBits.push(`Last repair ${formatDate(weld.last_repair_date)}`);
        }
        const metaText = metaBits.length ? metaBits.join(" • ") : "";
        let sourcesHtml = "";
        if (Array.isArray(weld.sources) && weld.sources.length) {
          const links = weld.sources
            .map(function (source) {
              const icon = source.type === "file" ? "bi-file-earmark-text" : "bi-link-45deg";
              const label = source.type === "file" ? "View source file" : "Open weld";
              const confidence = typeof source.parser_confidence === "number"
                ? `<span class="text-muted">(${(source.parser_confidence * 100).toFixed(0)}% confidence)</span>`
                : "";
              return `<span class="d-inline-flex align-items-center gap-1"><a href="${source.url}" class="small text-decoration-none" target="_blank" rel="noopener" title="${label}" aria-label="${label}"><i class="bi ${icon}"></i></a>${confidence}</span>`;
            })
            .join(" ");
          sourcesHtml = `<div class="small text-muted mt-1 d-flex flex-wrap align-items-center gap-2">${links}</div>`;
        }
        li.innerHTML = `
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div>
              <div class="fw-semibold">${identifier}</div>
              ${welder ? `<div class="small text-muted">Welder: ${welderLink}</div>` : ""}
              ${metaText ? `<div class="small text-muted">${metaText}</div>` : ""}
              ${sourcesHtml}
            </div>
            ${weldLink}
          </div>
        `;
        list.appendChild(li);
      });
    }

    function updatePagination(meta, append) {
      if (!meta) {
        paginationEl.textContent = "";
        showMoreBtn.hidden = true;
        return;
      }
      if (!append) {
        state.shown = 0;
      }
      state.perPage = meta.per_page || state.perPage;
      state.total = meta.total || 0;
      state.shown += meta.returned || 0;
      if (state.total === 0) {
        paginationEl.textContent = "";
        showMoreBtn.hidden = true;
        return;
      }
      const remaining = Math.max(state.total - state.shown, 0);
      paginationEl.textContent = `Showing ${state.shown} of ${state.total}. Click 'Show more' to load another ${Math.min(state.perPage, Math.max(remaining, 0))}.`;
      showMoreBtn.hidden = remaining <= 0;
    }

    function showError(message) {
      resetResults();
      answerEl.textContent = message || "Unable to load QUILT results.";
    }

    function buildPayload(overrides, append) {
      const scope = scopePayload();
      if (!scope.project_id && !scope.org_id) {
        showError("Select a project or organization to use QUILT.");
        return null;
      }
      const prompt = overrides.intent && quickPrompts[overrides.intent]
        ? quickPrompts[overrides.intent]
        : null;
      const payload = Object.assign({}, scope, {
        query: overrides.query || (prompt ? prompt.query : input.value.trim()),
        page: overrides.page || 1,
        per_page: overrides.per_page || (prompt && prompt.per_page ? prompt.per_page : state.perPage),
        top_n: 5,
      });
      const intentValue = overrides.intent
        ? (prompt ? prompt.intent : overrides.intent)
        : undefined;
      if (intentValue) {
        payload.intent = intentValue;
      }
      if (overrides.filters) {
        payload.filters = overrides.filters;
      }
      state.lastPayload = Object.assign({}, payload, { append: !!append });
      return payload;
    }

    function sendRequest(overrides, append) {
      const payload = buildPayload(overrides || {}, append);
      if (!payload) {
        return;
      }
      if (!append) {
        resetResults();
      }
      root.classList.add("is-loading");
      fetch("/api/quilt/query/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      })
        .then(function (response) {
          if (!response.ok) {
            return response.json().catch(function () {
              return { error: "Unable to load QUILT data." };
            });
          }
          return response.json();
        })
        .then(function (data) {
          if (!data) {
            showError("No response from QUILT.");
            return;
          }
          if (data.error) {
            showError(data.error);
            return;
          }
          answerEl.textContent = data.answer || "";
          renderKpi(data.kpi);
          renderWelds(data.welds, append);
          updatePagination(data.meta, append);
        })
        .catch(function () {
          showError("Unexpected error while contacting QUILT.");
        })
        .finally(function () {
          root.classList.remove("is-loading");
        });
    }

    toggleButton.addEventListener("click", function () {
      toggle(true);
      if (!state.lastPayload) {
        sendRequest({ intent: "repairs_summary" }, false);
      }
    });

    closeButton.addEventListener("click", function () {
      toggle(false);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      toggle(true);
      sendRequest({ query: input.value.trim() }, false);
    });

    quickButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        const intent = button.dataset.intent;
        if (!intent) {
          return;
        }
        toggle(true);
        const prompt = quickPrompts[intent];
        if (prompt && prompt.query) {
          input.value = prompt.query;
        }
        sendRequest({ intent: intent }, false);
      });
    });

    showMoreBtn.addEventListener("click", function () {
      if (!state.lastPayload) {
        return;
      }
      const nextPage = (state.lastPayload.page || 1) + 1;
      const overrides = Object.assign({}, state.lastPayload, { page: nextPage });
      delete overrides.append;
      sendRequest(overrides, true);
    });

    // Allow escape key to close the widget when focused.
    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        toggle(false);
      }
    });
  });
})();
