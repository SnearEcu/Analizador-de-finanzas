(function () {
  const endpoints = window.APP_ENDPOINTS || {};
  const state = {
    summary: null,
    statements: [],
    movements: [],
    suggestions: [],
    mailboxes: []
  };

  const money = new Intl.NumberFormat("es-EC", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2
  });

  const toText = (value) => {
    if (value == null || value === "") return "-";
    if (typeof value === "number") return money.format(value);
    return String(value);
  };

  const qs = (sel, root = document) => root.querySelector(sel);
  const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function setSyncStatus(mailbox, message = "", tone = "") {
    const node = qs(`[data-sync-status="${mailbox}"]`);
    if (!node) return;
    node.textContent = message;
    node.classList.remove("active", "success", "error");
    if (tone) node.classList.add(tone);
  }

  function setMailboxBusy(mailbox, busy) {
    qsa(`[data-mailbox="${mailbox}"]`).forEach((button) => {
      if (button.tagName === "BUTTON") {
        button.disabled = busy;
      }
    });
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, {
      headers: {
        Accept: "application/json",
        ...(options.headers || {})
      },
      ...options
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status} en ${url}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return null;
    }

    const text = await response.text();
    if (!text.trim()) {
      return null;
    }

    try {
      return JSON.parse(text);
    } catch (error) {
      console.error("Respuesta JSON inválida:", url, error);
      return null;
    }
  }

  function setMetrics(summary) {
    const mapping = {
      income: summary?.income_total,
      expenses: summary?.expense_total,
      payments: summary?.payment_total,
      transfers: summary?.transfer_total,
      net: summary?.net_total
    };

    Object.entries(mapping).forEach(([key, value]) => {
      const node = qs(`[data-summary="${key}"]`);
      if (node) node.textContent = toText(value);
    });

    const values = Object.values(mapping)
      .map((item) => Math.abs(Number(item || 0)))
      .filter((item) => Number.isFinite(item));
    const max = Math.max(...values, 1);
    Object.entries(mapping).forEach(([key, value]) => {
      const bar = qs(`[data-bar="${key}"]`);
      if (!bar) return;
      const pct = Math.min(100, Math.round((Math.abs(Number(value || 0)) / max) * 100));
      bar.style.width = `${pct}%`;
    });

    const periodNode = qs("#summary-period");
    if (periodNode && summary?.period_label) {
      periodNode.textContent = summary.period_label;
    }

    const sourceNode = qs("#summary-source");
    if (sourceNode && summary?.source_label) {
      sourceNode.textContent = summary.source_label;
    }
  }

  function formatStatementRow(item) {
    const period = item.period_start && item.period_end
      ? `${item.period_start} - ${item.period_end}`
      : item.period_label || "-";

    return `
      <tr>
        <td>${item.institution_label || item.institution || "-"}</td>
        <td>${item.owner_label || item.owner || "-"}</td>
        <td>${period}</td>
        <td>${item.cutoff_date || "-"}</td>
        <td>${toText(item.minimum_payment)}</td>
        <td>${toText(item.total_payment)}</td>
        <td>${item.source_type || "-"}</td>
      </tr>
    `;
  }

  function formatMovementRow(item) {
    const typeClass = item.movement_type === "payment"
      ? "good"
      : item.movement_type === "transfer"
        ? "warn"
        : "bad";
    return `
      <tr>
        <td>${item.posted_at || "-"}</td>
        <td>${item.institution_label || item.institution || "-"}</td>
        <td>${item.owner_label || item.owner || "-"}</td>
        <td>${item.description_raw || "-"}</td>
        <td><span class="tag ${typeClass}">${item.movement_type || "-"}</span></td>
        <td>${toText(item.amount)}</td>
        <td>${item.source_type || "-"}</td>
        <td>${item.confidence != null ? `${Math.round(Number(item.confidence) * 100)}%` : "-"}</td>
      </tr>
    `;
  }

  function formatSuggestionRow(item) {
    return `
      <tr>
        <td>${item.suggestion_type || "-"}</td>
        <td>${item.source_ref || item.reference || "-"}</td>
        <td>${item.suggestion_label || item.suggestion || "-"}</td>
        <td>${item.confidence != null ? `${Math.round(Number(item.confidence) * 100)}%` : "-"}</td>
      </tr>
    `;
  }

  function renderTable(selector, rows, formatter, emptyMessage) {
    const tbody = qs(selector);
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="${tbody.closest("table")?.querySelectorAll("thead th").length || 1}">${emptyMessage}</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map(formatter).join("");
  }

  async function loadSummary() {
    if (!endpoints.summary) return;
    const data = await requestJson(endpoints.summary);
    state.summary = data;
    setMetrics(data);
  }

  async function loadStatements() {
    if (!endpoints.statements) return;
    const data = await requestJson(endpoints.statements);
    state.statements = Array.isArray(data) ? data : data.items || [];
    renderTable("#statements-table", state.statements, formatStatementRow, "Esperando importación.");
  }

  async function loadMovements() {
    if (!endpoints.movements) return;
    const data = await requestJson(endpoints.movements);
    state.movements = Array.isArray(data) ? data : data.items || [];
    renderTable("#movements-table", state.movements, formatMovementRow, "Sin movimientos cargados.");
  }

  async function loadSuggestions() {
    if (!endpoints.suggestions) return;
    const data = await requestJson(endpoints.suggestions);
    state.suggestions = Array.isArray(data) ? data : data.items || [];
    renderTable("#suggestions-table", state.suggestions, formatSuggestionRow, "Sin sugerencias todavía.");
  }

  function renderMailboxes(mailboxes) {
    state.mailboxes = Array.isArray(mailboxes) ? mailboxes : [];
    const byRole = Object.fromEntries(state.mailboxes.map((item) => [item.role, item]));
    ["owner", "spouse"].forEach((role) => {
      const mailbox = byRole[role];
      const button = qs(`[data-action="connect-gmail"][data-mailbox="${role}"]`);
      const copy = qs(`[data-mailbox-copy="${role}"]`);
      if (!button) return;
      if (mailbox?.connected) {
        button.textContent = "Conectado";
        button.dataset.connected = "true";
        button.title = mailbox.email_address || "Conectado";
      } else {
        button.textContent = "Conectar";
        button.dataset.connected = "false";
        button.title = "";
      }
      if (copy) {
        if (mailbox?.connected) {
          const syncText = mailbox.last_synced_at
            ? `Última sincronización: ${new Date(mailbox.last_synced_at).toLocaleString("es-EC")}.`
            : "Conectado, pendiente de primera sincronización.";
          copy.textContent = `${mailbox.email_address || "Cuenta Gmail"} conectada. ${syncText}`;
        } else if (role === "owner") {
          copy.textContent = "Conecta el buzón principal para bajar estados y transferencias.";
        } else {
          copy.textContent = "Usada para transferencias y documentos de la otra titular.";
        }
      }
    });
  }

  async function loadMailboxes() {
    if (!endpoints.mailboxes) return;
    const data = await requestJson(endpoints.mailboxes);
    renderMailboxes(data);
  }

  async function refreshAll() {
    await Promise.allSettled([loadSummary(), loadStatements(), loadMovements(), loadSuggestions(), loadMailboxes()]);
  }

  async function connectGmail(mailbox) {
    if (!endpoints.gmailConnect) return;
    const existing = state.mailboxes.find((item) => item.role === mailbox && item.connected);
    if (existing) {
      const reconnect = window.confirm(`La cuenta ${existing.email_address} ya está conectada. ¿Quieres reconectarla?`);
      if (!reconnect) return;
    }
    const url = new URL(endpoints.gmailConnect, window.location.origin);
    if (mailbox) url.searchParams.set("mailbox", mailbox);
    const ownerLabel = mailbox === "spouse" ? "Titular 2" : "Titular 1";
    const ownerName = window.prompt(`Nombre para ${ownerLabel}:`, mailbox === "spouse" ? "Sheerlayn Chiriboga" : "Bryan Ortega");
    if (!ownerName) return;
    const email = window.prompt(`Correo Gmail para ${ownerLabel}:`, "");
    if (!email) return;
    url.searchParams.set("owner_name", ownerName);
    url.searchParams.set("email_address", email);
    window.location.href = url.toString();
  }

  async function syncMail(mailbox) {
    if (!endpoints.gmailSync) return;
    const payload = mailbox ? { mailbox, max_results: 100 } : { max_results: 100 };
    setMailboxBusy(mailbox, true);
    setSyncStatus(mailbox, "Sincronizando Gmail y revisando adjuntos...", "active");
    try {
      const result = await requestJson(endpoints.gmailSync, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const scanned = Number(result?.scanned_messages || 0);
      const attachments = Number(result?.pdf_attachments_found || 0);
      const imported = Number(result?.imported_statements || 0);
      const duplicates = Number(result?.duplicate_statements || 0);
      const skipped = Number(result?.skipped_statements || 0);
      const transfers = Number(result?.detected_transfers || 0);
      setSyncStatus(
        mailbox,
        `Listo: ${scanned} correos revisados, ${attachments} PDFs encontrados, ${imported} estados importados, ${duplicates} repetidos, ${skipped} no soportados, ${transfers} transferencias detectadas.`,
        "success"
      );
      await refreshAll();
    } catch (error) {
      const message = error?.message || "Error al sincronizar";
      setSyncStatus(mailbox, message, "error");
      throw error;
    } finally {
      setMailboxBusy(mailbox, false);
    }
  }

  async function uploadStatement(form) {
    if (!endpoints.uploadStatement) return;
    const formData = new FormData(form);
    await requestJson(endpoints.uploadStatement, {
      method: "POST",
      body: formData
    });
    form.reset();
    await refreshAll();
  }

  function applyFilters() {
    const owner = qs("#owner-filter")?.value || "";
    const institution = qs("#institution-filter")?.value || "";
    const type = qs("#type-filter")?.value || "";
    const search = (qs("#search-filter")?.value || "").trim().toLowerCase();

    const filteredMovements = state.movements.filter((item) => {
      const searchable = [
        item.owner,
        item.owner_label,
        item.institution,
        item.institution_label,
        item.description_raw,
        item.source_type,
        item.account_label
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      if (owner && item.owner !== owner) return false;
      if (institution && item.institution !== institution) return false;
      if (type && item.movement_type !== type) return false;
      if (search && !searchable.includes(search)) return false;
      return true;
    });

    renderTable("#movements-table", filteredMovements, formatMovementRow, "Sin movimientos cargados.");
  }

  function bindActions() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      const mailbox = button.dataset.mailbox || "";

      if (action === "open-upload") {
        const input = qs('#upload-form input[type="file"]');
        input?.click();
      }
      if (action === "connect-gmail") {
        connectGmail(mailbox).catch(console.error);
      }
      if (action === "sync-mail") {
        syncMail(mailbox).catch(console.error);
      }
      if (action === "refresh-data") {
        refreshAll().catch(console.error);
      }
    });

    const uploadForm = qs("#upload-form");
    uploadForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      uploadStatement(uploadForm).catch(console.error);
    });

    ["#owner-filter", "#institution-filter", "#type-filter", "#search-filter"].forEach((selector) => {
      const field = qs(selector);
      field?.addEventListener("input", applyFilters);
      field?.addEventListener("change", applyFilters);
    });
  }

  async function boot() {
    bindActions();
    await refreshAll();
  }

  document.addEventListener("DOMContentLoaded", () => {
    boot().catch((error) => {
      console.error(error);
    });
  });
})();
