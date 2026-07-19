const H = () => window.getPceHeaders({ "Content-Type": "application/json" });
let templateState = { templates: {}, labels: {}, placeholders: [] };
let runModeState = { mode: "monitoring_only", modes: [] };
let scopeState = {};
let quickConfigState = {};

function notify(message) {
  alert(message);
}

function setHealthStatus(text, cls) {
  const status = document.getElementById("healthStatus");
  status.textContent = text;
  status.className = `pill ${cls || "pill-neutral"}`;
}

async function fetchJSON(url, opts) {
  opts = opts || {};
  const r = await fetch(url, Object.assign({}, opts, { headers: Object.assign({}, H(), opts.headers || {}) }));
  if (!r.ok) {
    let detail = "";
    try {
      const contentType = r.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const body = await r.json();
        detail = body.detail || body.error || JSON.stringify(body);
      } else {
        detail = (await r.text()).trim();
      }
    } catch (_) {}
    throw new Error(detail ? `${r.status} - ${detail}` : String(r.status));
  }
  return r.json();
}

async function postAction(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: H(),
    body: body ? JSON.stringify(body) : undefined
  });
  if (!r.ok) {
    let detail = "";
    try {
      const contentType = r.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const payload = await r.json();
        detail = payload.detail || payload.error || JSON.stringify(payload);
      } else {
        detail = (await r.text()).trim();
      }
    } catch (_) {}
    throw new Error(detail ? `${r.status} - ${detail}` : String(r.status));
  }
  return r.json();
}

function selectedKey() {
  return document.getElementById("templateKey").value;
}

function fillEditor() {
  const key = selectedKey();
  const tpl = templateState.templates[key];
  if (!tpl) return;
  document.getElementById("templateSubject").value = tpl.subject || "";
  document.getElementById("templateHtml").value = tpl.html || "";
  renderPreview();
}

async function loadTemplates() {
  const data = await fetchJSON("/api/settings/email-templates");
  templateState = data;
  document.getElementById("emailTemplatePath").textContent = data.path;
  document.getElementById("templatePlaceholders").textContent = (data.placeholders || []).join("  ");
  fillEditor();
}

function renderRunMode(data) {
  runModeState = data;
  const indicator = document.getElementById("runModeIndicator");
  indicator.textContent = data.label || data.mode;
  indicator.className = "pill " + (data.badge_class || "pill-neutral");
  document.getElementById("runModeIndicatorText").textContent = data.label || data.mode;
  document.getElementById("runModeCurrentNote").textContent = data.summary || "-";
  document.getElementById("runModeConfigPath").textContent = data.path || "-";
  document.getElementById("runModeSelect").value = data.mode || "monitoring_only";
  document.getElementById("runModeSummary").textContent = data.summary || "-";
  renderRunModeCards();
  renderSyncStatus(data.sync_status || {});

  const effects = document.getElementById("runModeEffects");
  effects.innerHTML = "";
  (data.effects || []).forEach((effect) => {
    const li = document.createElement("li");
    li.textContent = effect;
    effects.appendChild(li);
  });

  const ctx = data.policy_context || {};
  document.getElementById("runModePolicyContext").innerHTML = [
    ["Warning Days", Array.isArray(ctx.warning_days) ? ctx.warning_days.join(", ") : "-"],
    ["Force Change Day", ctx.force_change_day ?? "-"],
    ["Grace Days", ctx.grace_days ?? "-"],
    ["After Grace", ctx.action_after_grace || "-"]
  ].map(([label, value]) => (
    `<div><span class="lbl">${label}</span><span class="mono">${value}</span></div>`
  )).join("");
  renderSchedulerStatus(data.scheduler || {});
  updateRunModePreview();
}

function renderRunModeError(message) {
  document.getElementById("runModeIndicator").textContent = "Error";
  document.getElementById("runModeIndicator").className = "pill pill-danger";
  document.getElementById("runModeIndicatorText").textContent = "Mode failed to load";
  document.getElementById("runModeCurrentNote").textContent = "Check the API token or backend connection, then refresh the page.";
  document.getElementById("runModeConfigPath").textContent = "-";
  document.getElementById("runModeCards").innerHTML = `<div class="mode-card mode-card-loading">Failed to load mode (${message})</div>`;
  document.getElementById("runModeSummary").textContent = `Mode data is unavailable (${message}).`;
  document.getElementById("runModeEffects").innerHTML = "<li>Mode data is not available yet.</li>";
  document.getElementById("runModePolicyContext").innerHTML = [
    "Warning Days",
    "Force Change Day",
    "Grace Days",
    "After Grace"
  ].map((label) => `<div><span class="lbl">${label}</span><span class="mono">-</span></div>`).join("");
  document.getElementById("syncStatusGrid").innerHTML = [
    "Status", "Action", "Started", "Finished"
  ].map((label) => `<div><span class="lbl">${label}</span><span class="mono">-</span></div>`).join("");
  document.getElementById("schedulerStatusGrid").innerHTML = [
    "Frequency", "Next Run", "Timezone", "Cron"
  ].map((label) => `<div><span class="lbl">${label}</span><span class="mono">-</span></div>`).join("");
  document.getElementById("schedulerStatusNote").textContent = "Scheduler status is not available yet.";
}

function selectedRunModeMeta() {
  const selected = document.getElementById("runModeSelect").value;
  return (runModeState.modes || []).find((item) => item.key === selected) || null;
}

function setSelectedRunMode(modeKey) {
  document.getElementById("runModeSelect").value = modeKey;
  updateRunModePreview();
}

function renderRunModeCards() {
  const wrap = document.getElementById("runModeCards");
  const selected = document.getElementById("runModeSelect").value;
  wrap.innerHTML = (runModeState.modes || []).map((item) => `
    <button
      type="button"
      class="mode-card ${selected === item.key ? "active" : ""}"
      data-mode-key="${item.key}"
    >
      <div class="mode-card-top">
        <span class="pill ${item.badge_class || "pill-neutral"}">${item.label}</span>
        ${runModeState.mode === item.key ? '<span class="mode-card-live">Active</span>' : ""}
      </div>
      <div class="mode-card-body">
        <strong>${item.label}</strong>
        <p>${item.summary}</p>
      </div>
    </button>
  `).join("");
  wrap.querySelectorAll("[data-mode-key]").forEach((node) => {
    node.addEventListener("click", () => setSelectedRunMode(node.getAttribute("data-mode-key")));
  });
}

function updateRunModePreview() {
  const meta = selectedRunModeMeta();
  if (!meta) return;
  document.getElementById("runModeSummary").textContent = meta.summary || "-";
  const effects = document.getElementById("runModeEffects");
  effects.innerHTML = (meta.effects || []).map((effect) => `<li>${effect}</li>`).join("");
  const actual = (runModeState.modes || []).find((item) => item.key === runModeState.mode);
  const note = document.getElementById("runModeSelectionNote");
  if (actual && meta.key !== actual.key) {
    note.textContent = `Selection not saved yet. Current active mode: ${actual.label}. New mode to be saved: ${meta.label}.`;
    note.className = "mode-selection-note pending";
  } else {
    note.textContent = `The current active mode is already set to: ${meta.label}.`;
    note.className = "mode-selection-note";
  }
  renderRunModeCards();
}

function renderSyncStatus(sync) {
  document.getElementById("syncStatusGrid").innerHTML = [
    ["Status", sync.status || "idle"],
    ["Action", sync.action || "sync_directory"],
    ["Started", window.formatDateTime(sync.started_at)],
    ["Finished", window.formatDateTime(sync.finished_at)]
  ].map(([label, value]) => `<div><span class="lbl">${label}</span><span class="mono">${value || "-"}</span></div>`).join("");
}

function renderSchedulerStatus(schedule) {
  document.getElementById("schedulerStatusGrid").innerHTML = [
    ["Frequency", schedule.frequency_label || "-"],
    ["Next Run", window.formatDateTime(schedule.next_run_at)],
    ["Timezone", schedule.timezone || "-"],
    ["Cron", schedule.cron || "-"]
  ].map(([label, value]) => `<div><span class="lbl">${label}</span><span class="mono">${value || "-"}</span></div>`).join("");
  document.getElementById("schedulerStatusNote").textContent = schedule.threshold_note || "-";
}

function linesToArray(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function arrayToLines(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function parseCommaNumberList(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item));
}

function renderQuickConfig(data) {
  quickConfigState = data;
  document.getElementById("quickConfigPath").textContent = data.path || "-";
  document.getElementById("qcMaxPasswordAgeDays").value = data.policy?.max_password_age_days ?? "";
  document.getElementById("qcWarningDays").value = Array.isArray(data.policy?.warning_days) ? data.policy.warning_days.join(", ") : "";
  document.getElementById("qcGraceDays").value = data.policy?.grace_period_days_after_expiry ?? "";
  document.getElementById("qcForceChangeDay").value = data.policy?.force_change_at_logon_on_day ?? "";
  document.getElementById("qcActionAfterGrace").value = data.policy?.action_after_grace || "Disable";
  document.getElementById("qcAdServer").value = data.active_directory?.server || "";
  document.getElementById("qcAdPort").value = data.active_directory?.port ?? "";
  document.getElementById("qcAdBindUser").value = data.active_directory?.bind_user || "";
  document.getElementById("qcAdBindPassword").value = "";
  document.getElementById("qcAdBindPassword").placeholder = data.active_directory?.bind_password_configured
    ? "Configured. Leave blank to keep current password"
    : "Enter bind password";
  document.getElementById("qcAdSearchBase").value = data.active_directory?.search_base || "";
  document.getElementById("qcAdSearchFilter").value = data.active_directory?.search_filter || "";
  document.getElementById("qcAdUseSsl").checked = !!data.active_directory?.use_ssl;
  document.getElementById("qcTenantId").value = data.m365?.tenant_id || "";
  document.getElementById("qcClientId").value = data.m365?.client_id || "";
  document.getElementById("qcReminderSender").value = data.m365?.reminder_sender || "";
  document.getElementById("qcRevokeSessionsOnLock").checked = !!data.m365?.revoke_sessions_on_lock;
  document.getElementById("qcFromAddress").value = data.notification?.from_address || "";
  document.getElementById("qcFromDisplayName").value = data.notification?.from_display_name || "";
  document.getElementById("qcDashboardBaseUrl").value = data.dashboard?.base_url || "";
  document.getElementById("qcAdminRecipients").value = arrayToLines(data.notification?.admin_recipients);
}

function collectQuickConfigPayload() {
  return {
    policy: {
      max_password_age_days: Number(document.getElementById("qcMaxPasswordAgeDays").value || 0),
      warning_days: parseCommaNumberList(document.getElementById("qcWarningDays").value),
      grace_period_days_after_expiry: Number(document.getElementById("qcGraceDays").value || 0),
      force_change_at_logon_on_day: Number(document.getElementById("qcForceChangeDay").value || 0),
      action_after_grace: document.getElementById("qcActionAfterGrace").value
    },
    active_directory: {
      server: document.getElementById("qcAdServer").value.trim(),
      port: Number(document.getElementById("qcAdPort").value || 0),
      bind_user: document.getElementById("qcAdBindUser").value.trim(),
      bind_password: document.getElementById("qcAdBindPassword").value ? document.getElementById("qcAdBindPassword").value : null,
      search_base: document.getElementById("qcAdSearchBase").value.trim(),
      search_filter: document.getElementById("qcAdSearchFilter").value.trim(),
      use_ssl: document.getElementById("qcAdUseSsl").checked
    },
    m365: {
      tenant_id: document.getElementById("qcTenantId").value.trim(),
      client_id: document.getElementById("qcClientId").value.trim(),
      reminder_sender: document.getElementById("qcReminderSender").value.trim(),
      revoke_sessions_on_lock: document.getElementById("qcRevokeSessionsOnLock").checked
    },
    notification: {
      from_address: document.getElementById("qcFromAddress").value.trim(),
      from_display_name: document.getElementById("qcFromDisplayName").value.trim(),
      admin_recipients: linesToArray(document.getElementById("qcAdminRecipients").value)
    },
    dashboard: {
      base_url: document.getElementById("qcDashboardBaseUrl").value.trim()
    }
  };
}

function renderQuickConfigTestResult(data) {
  document.getElementById("quickConfigTestGrid").innerHTML = [
    ["AD Status", `${data.active_directory?.ok ? "OK" : "FAILED"} | ${data.active_directory?.summary || "-"}`],
    ["Entra Status", `${data.entra?.ok ? "OK" : "FAILED"} | ${data.entra?.summary || "-"}`],
    ["Sender", data.sender?.configured_sender || "-"],
    ["Display Name Note", data.sender?.note || "-"]
  ].map(([label, value]) => `<div><span class="lbl">${label}</span><span class="mono">${value}</span></div>`).join("");
}

function renderSecretHealth(data) {
  document.getElementById("secretHealthSummary").innerHTML = [
    ["Configured", `${data.configured}/${data.total}`],
    ["Env Path", data.env_path || "-"]
  ].map(([label, value]) => `<div><span class="lbl">${label}</span><span class="mono">${value}</span></div>`).join("");

  document.getElementById("secretHealthBody").innerHTML = (data.items || []).map(item => {
    const cls = item.configured ? "pill-good" : "pill-danger";
    const label = item.configured ? "Configured" : "Missing";
    return `<tr>
      <td class="mono">${item.key}</td>
      <td><span class="pill ${cls}">${label}</span></td>
      <td>${item.source || "-"}</td>
      <td class="mono">${item.config_path || "-"}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="4" class="muted">No secret data</td></tr>';
}

function renderConfigDoctor(data) {
  document.getElementById("configDoctorSummary").innerHTML = [
    ["Overall", data.ok ? "Healthy" : "Needs Attention"],
    ["Version", data.version || "-"]
  ].map(([label, value]) => `<div><span class="lbl">${label}</span><span class="mono">${value}</span></div>`).join("");

  document.getElementById("configDoctorBody").innerHTML = (data.checks || []).map(item => {
    const cls = item.ok ? "pill-good" : "pill-danger";
    const label = item.ok ? "OK" : "Check";
    return `<tr>
      <td>${item.label}</td>
      <td><span class="pill ${cls}">${label}</span></td>
      <td>${item.summary || "-"}</td>
      <td>${item.suggestion || "-"}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="4" class="muted">No doctor result</td></tr>';
}

async function loadSecretHealth() {
  const data = await fetchJSON("/api/settings/secret-health");
  renderSecretHealth(data);
}

async function loadConfigDoctor() {
  const data = await fetchJSON("/api/settings/config-doctor");
  renderConfigDoctor(data);
}

async function loadQuickConfig() {
  const data = await fetchJSON("/api/settings/quick-config");
  renderQuickConfig(data);
}

async function saveQuickConfig() {
  const payload = collectQuickConfigPayload();
  const data = await fetchJSON("/api/settings/quick-config", {
    method: "PUT",
    body: JSON.stringify(payload)
  });
  renderQuickConfig(data);
  loadRunMode().catch(() => {});
  notify("Quick config saved");
}

async function testQuickConfig() {
  const payload = collectQuickConfigPayload();
  const data = await fetchJSON("/api/settings/quick-config/test", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  renderQuickConfigTestResult(data);
  notify(data.ok ? "Quick config test passed" : "Quick config test finished, but some items still need attention");
}

function renderScopeRules(data) {
  scopeState = data;
  document.getElementById("scopeTargetOu").textContent = data.target_ou || "-";
  document.getElementById("scopeSearchBase").textContent = data.search_base || "-";
  document.getElementById("scopeSearchFilter").textContent = data.search_filter || "-";
  document.getElementById("scopeRulesPath").textContent = data.path || "-";
  document.getElementById("scopeExcludedUsers").value = arrayToLines(data.excluded_users);
  document.getElementById("scopeExcludedGroups").value = arrayToLines(data.excluded_groups);
  document.getElementById("scopeWhitelistedUsers").value = arrayToLines(data.whitelisted_users);
  document.getElementById("scopeIncludeDisabledAccounts").checked = !!data.include_disabled_accounts;
}

async function loadScopeRules() {
  const data = await fetchJSON("/api/settings/scope-rules");
  renderScopeRules(data);
}

async function saveScopeRules() {
  const payload = {
    excluded_users: linesToArray(document.getElementById("scopeExcludedUsers").value),
    excluded_groups: linesToArray(document.getElementById("scopeExcludedGroups").value),
    whitelisted_users: linesToArray(document.getElementById("scopeWhitelistedUsers").value),
    include_disabled_accounts: document.getElementById("scopeIncludeDisabledAccounts").checked
  };
  const data = await fetchJSON("/api/settings/scope-rules", {
    method: "PUT",
    body: JSON.stringify(payload)
  });
  renderScopeRules(data);
  notify("Scope rules saved");
}

async function loadRunMode() {
  const data = await fetchJSON("/api/settings/run-mode");
  renderRunMode(data);
}

async function saveRunMode() {
  const run_mode = document.getElementById("runModeSelect").value;
  const data = await fetchJSON("/api/settings/run-mode", {
    method: "PUT",
    body: JSON.stringify({ run_mode })
  });
  renderRunMode(data);
  notify("Mode saved to config");
}

async function syncDirectoryNow() {
  await postAction("/api/control/sync-directory");
  renderSyncStatus({
    status: "running",
    action: "sync_directory",
    started_at: new Date().toISOString(),
    finished_at: null
  });
  notify("Directory sync started. Check Live Logs or refresh Users after it finishes.");
}

async function saveTemplates() {
  const key = selectedKey();
  templateState.templates[key].subject = document.getElementById("templateSubject").value;
  templateState.templates[key].html = document.getElementById("templateHtml").value;
  await fetchJSON("/api/settings/email-templates", {
    method: "PUT",
    body: JSON.stringify({ templates: { [key]: templateState.templates[key] } })
  });
  notify("Template saved");
}

function previewPayload() {
  const key = selectedKey();
  const sampleName = document.getElementById("templateSampleName").value.trim() || "Example User";
  const sampleUpn = document.getElementById("templateSampleUpn").value.trim() || "user@example.com";
  return {
    key,
    subject: document.getElementById("templateSubject").value,
    html: document.getElementById("templateHtml").value,
    display_name: sampleName,
    upn: sampleUpn,
    sam: sampleUpn.split("@", 1)[0],
    days_left: key === "warn_7" ? 7 : key === "warn_3" ? 3 : key === "warn_1" ? 1 : 7,
    deadline_text: "Tue, 14 Jul 2026 00:00:00 +0700"
  };
}

async function resetTemplates() {
  if (!confirm("Reset all email templates to default?")) return;
  await fetchJSON("/api/settings/email-templates/reset", { method: "POST" });
  await loadTemplates();
  notify("Templates reset to default");
}

async function renderPreview() {
  const key = selectedKey();
  templateState.templates[key].subject = document.getElementById("templateSubject").value;
  templateState.templates[key].html = document.getElementById("templateHtml").value;
  const preview = await fetchJSON("/api/settings/email-templates/preview", {
    method: "POST",
    body: JSON.stringify(previewPayload())
  });
  document.getElementById("templatePreviewSubject").textContent = preview.subject;
  document.getElementById("templatePreviewFrame").srcdoc = preview.html;
}

async function sendTestTemplate() {
  const to = document.getElementById("templateTestEmail").value.trim();
  if (!to) {
    notify("Enter a test destination email first");
    return;
  }
  const payload = previewPayload();
  payload.to = to;
  await fetchJSON("/api/settings/email-templates/send-test", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  notify("Test email sent to " + to);
}

async function testConnection() {
  const v = document.getElementById("apiToken").value.trim();
  try {
    const r = await fetch("/api/stats/kpi", { headers: { "X-API-Token": v } });
    if (r.ok) {
      setHealthStatus("Connected", "pill-good");
      return;
    }
    let detail = "";
    try {
      const contentType = r.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const body = await r.json();
        detail = body.detail || body.error || JSON.stringify(body);
      } else {
        detail = (await r.text()).trim();
      }
    } catch (_) {}
    setHealthStatus("Error " + r.status, "pill-danger");
    notify("Connection test failed: " + (detail || r.status));
  } catch (e) {
    setHealthStatus("Offline", "pill-danger");
    notify("Connection test failed: " + e.message);
  }
}

async function loadHealth() {
  try {
    const r = await fetch("/healthz");
    const data = await r.json();
    setHealthStatus(data.status || "Unknown", r.ok ? "pill-good" : "pill-danger");
  } catch (_) {
    setHealthStatus("Offline", "pill-danger");
  }
}

function bindSettingsEvents() {
  document.getElementById("btnSaveToken").addEventListener("click", () => {
    const v = document.getElementById("apiToken").value.trim();
    if (!v) return;
    localStorage.setItem("pce_token", v);
    notify("Token saved in this browser");
  });
  document.getElementById("btnSyncDirectory").addEventListener("click", syncDirectoryNow);
  document.getElementById("btnSaveScopeRules").addEventListener("click", saveScopeRules);
  document.getElementById("btnSaveQuickConfig").addEventListener("click", saveQuickConfig);
  document.getElementById("btnTestQuickConfig").addEventListener("click", testQuickConfig);
  document.getElementById("btnTestConn").addEventListener("click", testConnection);
  document.getElementById("btnRunDoctor").addEventListener("click", loadConfigDoctor);
  document.getElementById("btnSaveRunMode").addEventListener("click", saveRunMode);
  document.getElementById("templateKey").addEventListener("change", fillEditor);
  document.getElementById("btnSaveTemplate").addEventListener("click", saveTemplates);
  document.getElementById("btnPreviewTemplate").addEventListener("click", renderPreview);
  document.getElementById("btnSendTestTemplate").addEventListener("click", sendTestTemplate);
  document.getElementById("btnResetTemplate").addEventListener("click", resetTemplates);
  document.getElementById("templateSubject").addEventListener("input", renderPreview);
  document.getElementById("templateHtml").addEventListener("input", renderPreview);
  document.getElementById("templateSampleName").addEventListener("input", renderPreview);
  document.getElementById("templateSampleUpn").addEventListener("input", renderPreview);
  document.addEventListener("sse:run_completed", () => {
    loadRunMode().catch(() => {});
    loadConfigDoctor().catch(() => {});
  });
}

function seedSettingsDefaults() {
  document.getElementById("dashUrl").textContent = location.origin;
  document.getElementById("apiToken").value = localStorage.getItem("pce_token") || "";
  document.getElementById("templateSampleName").value = "Example User";
  document.getElementById("templateSampleUpn").value = "user@example.com";
  document.getElementById("templateTestEmail").value = "helpdesk@example.com";
}

function loadSettingsData() {
  loadHealth();
  loadRunMode().catch(e => renderRunModeError(e.message));
  loadQuickConfig().catch(e => notify("Failed to load quick config: " + e.message));
  loadSecretHealth().catch(e => notify("Failed to load secret health: " + e.message));
  loadConfigDoctor().catch(e => notify("Failed to load config doctor: " + e.message));
  loadScopeRules().catch(e => notify("Failed to load scope rules: " + e.message));
  loadTemplates().catch(e => notify("Failed to load templates: " + e.message));
}

seedSettingsDefaults();
bindSettingsEvents();
loadSettingsData();
