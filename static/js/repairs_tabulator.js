(function () {
  const DATE_FIELDS = new Set(["flagged_at", "repair_date", "last_attempt_date", "reinspection_passed_at"]);

  function parseConfig() {
    const script = document.getElementById("repair-log-config");
    if (!script) {
      return null;
    }
    try {
      return JSON.parse(script.textContent);
    } catch (error) {
      console.error("Failed to parse repair log config", error);
      return null;
    }
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toISOString().slice(0, 10);
  }

  function buildDetailTable(rows, headers) {
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headers.forEach((header) => {
      const th = document.createElement("th");
      th.textContent = header.label;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      headers.forEach((header) => {
        const td = document.createElement("td");
        const rawValue = row[header.field];
        td.textContent = DATE_FIELDS.has(header.field)
          ? formatDate(rawValue)
          : rawValue ?? "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function patchRepair(ctx, repairId, payload) {
    return fetch(ctx.repairUpdateUrlTemplate.replace("{id}", repairId), {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": ctx.csrfToken,
      },
      body: JSON.stringify(payload),
    }).then((response) => {
      if (!response.ok) {
        throw new Error("Failed to update repair");
      }
      return response.json();
    });
  }

  function postJson(ctx, template, repairId, payload) {
    return fetch(template.replace("{id}", repairId), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": ctx.csrfToken,
      },
      body: JSON.stringify(payload),
    }).then((response) => {
      if (!response.ok) {
        throw new Error("Request failed");
      }
      return response.json();
    });
  }

  function promptAttempt(ctx, rowData, refresh) {
    const performedAt = window.prompt(
      "Performed date (YYYY-MM-DD)",
      formatDate(rowData.repair_date) || formatDate(new Date()),
    );
    if (!performedAt) return;
    const welder = window.prompt("Welder stencil", rowData.repair_stencil || "");
    if (welder === null) return;
    const inches = window.prompt("Inches repaired", "");
    const payload = {
      performed_at: performedAt,
      welder_stencil: welder,
      outcome: "FAIL",
    };
    if (inches) payload.inches_repaired = inches;
    postJson(ctx, ctx.attemptCreateUrlTemplate, rowData.id, payload)
      .then(() => refresh())
      .catch((error) => {
        console.error(error);
        window.alert("Failed to record repair attempt");
      });
  }

  function promptReinspection(ctx, rowData, refresh) {
    const dateValue = window.prompt("Reinspection date (YYYY-MM-DD)", formatDate(new Date()));
    if (!dateValue) return;
    const rig = window.prompt("Reinspection rig", rowData.reinspection_nderig || "");
    if (rig === null) return;
    const reference = window.prompt("Report reference", rowData.last_reinspection_report_ref || "");
    if (reference === null) return;
    const result = window.prompt("Result (PASS/FAIL/HOLD)", "PASS");
    if (!result) return;
    const payload = {
      reinspection_date: dateValue,
      reinspection_nderig: rig,
      reinspection_ndereport_ref: reference,
      reinspection_result: result.toUpperCase(),
    };
    postJson(ctx, ctx.reinspectionCreateUrlTemplate, rowData.id, payload)
      .then(() => refresh())
      .catch((error) => {
        console.error(error);
        window.alert("Failed to record reinspection");
      });
  }

  function closeRepair(ctx, rowData, refresh) {
    const confirmClose = window.confirm("Close this repair?");
    if (!confirmClose) return;
    postJson(ctx, ctx.repairCloseUrlTemplate, rowData.id, {})
      .then(() => refresh())
      .catch((error) => {
        console.error(error);
        window.alert("Failed to close repair");
      });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const ctx = parseConfig();
    if (!ctx) {
      return;
    }

    const grid = document.getElementById("repair-log-grid");
    if (!grid) {
      return;
    }

    const assignedOptions = ctx.assignedOptions || [];
    const editableFields = new Set(ctx.editableFields || []);

    const table = new Tabulator(grid, {
      ajaxURL: ctx.projectRepairsUrl,
      ajaxParams: { per_page: ctx.perPage || 25, expand: "attempts,reinspections" },
      ajaxResponse(url, params, response) {
        this.setMaxPage(response.total_pages || 1);
        return response.data || [];
      },
      pagination: "remote",
      paginationSize: ctx.perPage || 25,
      layout: "fitColumns",
      placeholder: "No repairs logged",
      rowFormatter(row) {
        const data = row.getData();
        if (!data.attempts?.length && !data.reinspections?.length) {
          return;
        }
        let detail = row.getElement().querySelector(".repair-detail");
        if (!detail) {
          detail = document.createElement("div");
          detail.className = "repair-detail";
          row.getElement().appendChild(detail);
        }
        detail.innerHTML = "";
        if (data.attempts?.length) {
          const attemptsHeader = document.createElement("h6");
          attemptsHeader.textContent = "Repair Attempts";
          detail.appendChild(attemptsHeader);
          detail.appendChild(
            buildDetailTable(data.attempts, [
              { field: "attempt_no", label: "#" },
              { field: "performed_at", label: "Performed" },
              { field: "welder_stencil", label: "Welder" },
              { field: "inches_repaired", label: "Inches" },
              { field: "outcome", label: "Outcome" },
            ]),
          );
        }
        if (data.reinspections?.length) {
          const reinspectHeader = document.createElement("h6");
          reinspectHeader.textContent = "Reinspections";
          detail.appendChild(reinspectHeader);
          detail.appendChild(
            buildDetailTable(data.reinspections, [
              { field: "reinspection_date", label: "Date" },
              { field: "reinspection_nderig", label: "Rig" },
              { field: "reinspection_ndereport_ref", label: "Report" },
              { field: "reinspection_result", label: "Result" },
            ]),
          );
        }
      },
      columns: [
        { title: "Repair", field: "id", width: 90, hozAlign: "center" },
        { title: "Weld", field: "weld_identifier", width: 140 },
        { title: "Status", field: "status", width: 120 },
        { title: "Flagged", field: "flagged_at", sorter: "date", width: 120 },
        {
          title: "Repair Date",
          field: "repair_date",
          width: 120,
          editor: editableFields.has("repair_date") ? "input" : false,
        },
        {
          title: "Repair Stencil",
          field: "repair_stencil",
          width: 140,
          editor: editableFields.has("repair_stencil") ? "input" : false,
        },
        {
          title: "NDE Type",
          field: "nde_type",
          width: 110,
          editor: editableFields.has("nde_type") ? "input" : false,
        },
        {
          title: "Assigned",
          field: "assigned_to_id",
          width: 150,
          formatter(cell) {
            const value = cell.getValue();
            const option = assignedOptions.find((item) => item.value === value);
            return option ? option.label : "";
          },
          editor: editableFields.has("assigned_to_id") ? "list" : false,
          editorParams: {
            values: assignedOptions.reduce((acc, item) => {
              acc[item.value] = item.label;
              return acc;
            }, {}),
          },
        },
        { title: "Attempts", field: "attempt_count", width: 100, hozAlign: "center" },
        { title: "Inches", field: "total_inches_repaired", width: 110, hozAlign: "right" },
        { title: "Last Attempt", field: "last_attempt_date", width: 130 },
        { title: "Reinsp Result", field: "reinspection_result", width: 140 },
        { title: "Reinsp Pass", field: "reinspection_passed_at", width: 140 },
        {
          title: "Comments",
          field: "comments",
          widthGrow: 2,
          editor: editableFields.has("comments") ? "textarea" : false,
        },
        {
          title: "Actions",
          field: "actions",
          width: 210,
          formatter() {
            return `
              <div class="repair-actions">
                <button class="btn btn-outline-primary btn-sm" data-action="attempt">Attempt</button>
                <button class="btn btn-outline-secondary btn-sm" data-action="reinspect">Reinspect</button>
                <button class="btn btn-outline-success btn-sm" data-action="close">Close</button>
              </div>`;
          },
          cellClick(e, cell) {
            const button = e.target.closest("button[data-action]");
            if (!button) return;
            const action = button.dataset.action;
            const rowData = cell.getRow().getData();
            const refresh = () => cell.getRow().getTable().replaceData();
            if (action === "attempt") {
              promptAttempt(ctx, rowData, refresh);
            } else if (action === "reinspect") {
              promptReinspection(ctx, rowData, refresh);
            } else if (action === "close") {
              closeRepair(ctx, rowData, refresh);
            }
          },
        },
      ],
    });

    table.on("cellEdited", (cell) => {
      const field = cell.getField();
      if (!editableFields.has(field)) {
        return;
      }
      const repairId = cell.getRow().getData().id;
      const value = cell.getValue();
      const payload = {};
      if (field === "assigned_to_id") {
        payload.assigned_to_id = value || null;
      } else {
        payload[field] = value;
      }
      patchRepair(ctx, repairId, payload)
        .then((response) => {
          if (response && response.repair) {
            cell.getRow().update(response.repair);
          }
        })
        .catch((error) => {
          console.error(error);
          window.alert("Failed to update repair");
          cell.restoreOldValue();
        });
    });

    const refreshButton = document.getElementById("repair-log-refresh");
    if (refreshButton) {
      refreshButton.addEventListener("click", () => table.replaceData());
    }

    const exportButton = document.getElementById("repair-log-export");
    if (exportButton) {
      exportButton.addEventListener("click", () => {
        table.download("csv", "repair-log.csv");
      });
    }
  });
})();
