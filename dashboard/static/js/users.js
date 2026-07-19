const H = () => window.getPceHeaders({ "Content-Type": "application/json" });
const state = { q:"", status:"all", page:1, size:50, sortBy:"days_until_expiry", sortDir:"asc", total:0, users:[], selected: new Set() };

async function fetchJSON(url, opts) {
  opts = opts || {};
  const r = await fetch(url, Object.assign({}, opts, { headers: Object.assign({}, H(), opts.headers||{}) }));
  if (r.status === 401) throw new Error("401 Unauthorized - set API token in Settings");
  if (!r.ok) {
    let detail = "";
    const contentType = r.headers.get("content-type") || "";
    try {
      if (contentType.includes("application/json")) {
        const body = await r.json();
        detail = body.detail || body.error || JSON.stringify(body);
      } else {
        detail = (await r.text()).trim();
      }
    } catch (_) {}
    throw new Error(detail ? (r.status + " - " + detail) : String(r.status));
  }
  return r.json();
}
function fmtDate(iso) { return window.formatDateOnly(iso); }
function fmtDateTime(iso) { return window.formatDateTime(iso); }
function fmtRel(d) { if (d==null) return "-"; if (d<0) return Math.abs(d)+"d ago"; if (d===0) return "Today"; return d+"d"; }
function statusOf(u) {
  if (u.is_disabled) return {label:"Disabled", cls:"pill-danger"};
  if (u.is_locked) return {label:"Locked", cls:"pill-danger"};
  if (u.must_change_at_logon) return {label:"Must Change", cls:"pill-warn"};
  const d = u.days_until_expiry;
  if (d==null) {
    if (u.status_reason === "missing_password_dates") return {label:"No Password Date", cls:"pill-neutral"};
    return {label:"Unknown", cls:"pill-neutral"};
  }
  if (d<0) return {label:"Expired", cls:"pill-danger"};
  if (d<=1) return {label:"Critical", cls:"pill-danger"};
  if (d<=7) return {label:"Expiring", cls:"pill-warn"};
  return {label:"Compliant", cls:"pill-good"};
}
function daysCls(d) { if (d==null) return ""; if (d<=1) return "days-danger"; if (d<=7) return "days-warn"; return "days-good"; }
function initials(n) { return (n||"?").split(/[\s._-]+/).filter(Boolean).slice(0,2).map(s=>s[0].toUpperCase()).join(""); }
function emailStatusMeta(status, action, templateKey) {
  const source =
    action === "TestEmail" ? "Test" :
    action === "ManualNotify" ? "Manual" :
    action === "Warned" || action === "ForcedChange" ? "Auto" :
    "";
  const detail =
    templateKey === "warn_7" ? "7 Days" :
    templateKey === "warn_3" ? "3 Days" :
    templateKey === "warn_1" ? "1 Day" :
    templateKey === "expired" ? "Expired" :
    templateKey || "";
  if (status === "sent") return { cls: "pill-good", label: source ? `${source} Sent` : "Sent", hint: detail };
  if (status === "failed") return { cls: "pill-danger", label: source ? `${source} Failed` : "Failed", hint: detail };
  if (status === "skipped") return { cls: "pill-neutral", label: source ? `${source} Skipped` : "Skipped", hint: detail };
  return { cls: "pill-neutral", label: "Not sent", hint: "-" };
}
function latestEmailActivityLabel(status, action, templateKey, attemptNo) {
  if (!action) return "-";
  const meta = emailStatusMeta(status, action, templateKey);
  const base = meta.label;
  const detail = meta.hint && meta.hint !== "-" ? meta.hint : (templateKey || "-");
  return `${base} ${detail} #${attemptNo || 1}`.trim();
}
function avatarColor(s) {
  const c = ["#6366f1","#8b5cf6","#ec4899","#f43f5e","#f59e0b","#22c55e","#0ea5e9","#14b8a6"];
  let h=0; for (let i=0;i<(s||"").length;i++) h=(h*31+s.charCodeAt(i))>>>0;
  return c[h%c.length];
}
function esc(s) { return String(s==null?"":s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function debounce(fn, ms) { let t; return function() { const a = arguments; clearTimeout(t); t=setTimeout(()=>fn.apply(null,a),ms); }; }
function totalPages() { return Math.max(1, Math.ceil(state.total / state.size)); }
function generateStrongPassword(length) {
  const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const lower = "abcdefghijkmnopqrstuvwxyz";
  const digits = "23456789";
  const symbols = "!@#$%^&*_-+=?";
  const all = upper + lower + digits + symbols;
  const size = Math.max(16, length || 18);
  const bytes = new Uint32Array(size);
  window.crypto.getRandomValues(bytes);
  const chars = [
    upper[bytes[0] % upper.length],
    lower[bytes[1] % lower.length],
    digits[bytes[2] % digits.length],
    symbols[bytes[3] % symbols.length]
  ];
  for (let i = chars.length; i < size; i++) chars.push(all[bytes[i] % all.length]);
  for (let i = chars.length - 1; i > 0; i--) {
    const j = bytes[i] % (i + 1);
    const tmp = chars[i];
    chars[i] = chars[j];
    chars[j] = tmp;
  }
  return chars.join("");
}
async function copyTextWithFallback(value) {
  if (!value) return "unavailable";

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return "clipboard";
    } catch (_) {
      // Fall back to legacy copy path below.
    }
  }

  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  helper.style.pointerEvents = "none";
  helper.style.left = "-9999px";
  document.body.appendChild(helper);
  helper.focus();
  helper.select();
  helper.setSelectionRange(0, helper.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  } finally {
    document.body.removeChild(helper);
  }

  if (!copied) {
    try {
      window.prompt("Copy password manually (Ctrl+C, Enter):", value);
      return "prompt";
    } catch (_) {
      // Ignore prompt failures and return false below.
    }
  }

  return copied ? "clipboard" : "unavailable";
}
function actionMeta(action) {
  const map = {
    Disabled: { cls: "pill-danger", label: "Disabled" },
    Enabled: { cls: "pill-good", label: "Enabled" },
    Warned: { cls: "pill-warn", label: "Warned" },
    ForcedChange: { cls: "pill-warn", label: "Force Change" },
    PasswordReset: { cls: "pill-good", label: "Password Reset" },
    Unlocked: { cls: "pill-good", label: "Unlocked" },
    ManualNotify: { cls: "pill-good", label: "Manual Email" }
  };
  return map[action] || { cls: "pill-neutral", label: action || "-" };
}
function reasonLabel(reason) {
  const map = {
    must_change_at_next_logon: "User must change password at next logon",
    missing_password_dates: "Password last-set/expiry attributes are not available from AD",
    password_last_set_derived_from_expiry: "Password last-set is estimated from expiry because AD did not return pwdLastSet directly"
  };
  return map[reason] || "-";
}
function buildUserRow(u) {
  const s = statusOf(u);
  const sel = state.selected.has(u.sam);
  const primaryStatus = u.last_auto_email_status || u.last_email_status;
  const primaryAction = u.last_auto_email_action || u.last_email_action;
  const primaryTemplate = u.last_auto_email_template || u.last_email_template;
  const primaryAttempt = u.last_auto_email_attempt || u.last_email_attempt;
  const emailMeta = emailStatusMeta(primaryStatus, primaryAction, primaryTemplate);
  const emailHint = primaryTemplate ? ((emailMeta.hint || primaryTemplate) + " #" + (primaryAttempt || 1)) : "-";
  const latestActivity = latestEmailActivityLabel(u.last_email_status, u.last_email_action, u.last_email_template, u.last_email_attempt);
  const latestActivityLine =
    u.last_email_action && (u.last_email_action !== primaryAction || u.last_email_template !== primaryTemplate || u.last_email_attempt !== primaryAttempt)
      ? '<div class="user-sub">Latest activity: ' + esc(latestActivity) + '</div>'
      : "";

  return '<tr class="'+(sel?"row-selected":"")+'">'+
    '<td class="col-check"><input type="checkbox" class="chk chk-user" data-sam="'+u.sam+'" '+(sel?"checked":"")+'></td>'+
    '<td><div class="user-cell"><div class="avatar" style="background:'+avatarColor(u.sam)+'">'+initials(u.display_name||u.sam)+'</div>'+
      '<div><div class="user-name">'+esc(u.display_name||u.sam)+'</div><div class="user-sub">'+esc(u.email||"-")+'</div></div></div></td>'+
    '<td class="mono">'+esc(u.upn||"-")+'</td>'+
    '<td>'+fmtDate(u.password_last_set)+'</td>'+
    '<td>'+fmtDate(u.password_expiry)+'</td>'+
    '<td><span class="days-badge '+daysCls(u.days_until_expiry)+'">'+fmtRel(u.days_until_expiry)+'</span></td>'+
    '<td><span class="pill '+s.cls+'">'+s.label+'</span></td>'+
    '<td><span class="pill '+emailMeta.cls+'">'+emailMeta.label+'</span><div class="user-sub">'+esc(emailHint)+'</div>' + latestActivityLine + '</td>'+
    '<td class="col-actions"><div class="action-group">'+
      '<button class="btn btn-action btn-sm" onclick="openModal(\''+u.sam+'\')">View</button>'+
      '<button class="btn btn-action btn-sm" onclick="qa(\''+u.sam+'\',\'notify\')">Notify</button>'+
      '<button class="btn btn-action btn-sm btn-action-warn" onclick="qa(\''+u.sam+'\',\'force\')">Force Change</button>'+
      '<button class="btn btn-action btn-sm btn-action-primary" onclick="openPasswordDialog(\''+u.sam+'\')">Change Password</button>'+
      (u.is_disabled
        ? '<button class="btn btn-action btn-sm btn-action-info" onclick="qa(\''+u.sam+'\',\'enable\')">Enable</button>'
        : (u.is_locked ? '<button class="btn btn-action btn-sm btn-action-info" onclick="qa(\''+u.sam+'\',\'unlock\')">Unlock</button>'
                       : '<button class="btn btn-action btn-sm btn-action-danger" onclick="qa(\''+u.sam+'\',\'disable\')">Disable</button>'))+
    '</div>'+
    '</td></tr>';
}

function buildEmailHistoryRows(emailHist) {
  if (!emailHist.length) {
    return '<tr><td colspan="5" class="muted">No email history yet</td></tr>';
  }
  return emailHist.map(h => {
    const em = emailStatusMeta(h.status);
    return '<tr><td>'+fmtDateTime(h.sent_at)+'</td><td>'+esc(h.template_key||'-')+'</td><td>#'+esc(h.attempt_no||1)+'</td><td><span class="pill '+em.cls+'">'+em.label+'</span></td><td class="muted">'+esc(h.error||'-')+'</td></tr>';
  }).join("");
}

function buildPolicyHistoryRows(hist) {
  if (!hist.length) {
    return '<tr><td colspan="4" class="muted">No policy history yet</td></tr>';
  }
  return hist.map(h => {
    const a = actionMeta(h.action);
    return '<tr><td>'+fmtDateTime(h.timestamp)+'</td><td><span class="pill '+a.cls+'">'+a.label+'</span></td><td>'+h.days_left+'</td><td class="mono muted">'+esc(h.run_id)+'</td></tr>';
  }).join("");
}

function buildUserDetailBody(u, hist, emailHist) {
  const s = statusOf(u);
  const emailMeta = emailStatusMeta(u.last_email_status, u.last_email_action, u.last_email_template);
  return '<div class="modal-user-head"><div class="avatar avatar-lg" style="background:'+avatarColor(u.sam)+'">'+initials(u.display_name||u.sam)+'</div>'+
    '<div><h2>'+esc(u.display_name||u.sam)+'</h2><div class="muted mono">'+esc(u.upn||"-")+'</div>'+
    '<div><span class="pill '+s.cls+'">'+s.label+'</span></div></div></div>'+
    '<div class="detail-grid">'+
    '<div><span class="lbl">SAM:</span> <span class="mono">'+esc(u.sam)+'</span></div>'+
    '<div><span class="lbl">Email:</span> '+esc(u.email||"-")+'</div>'+
    '<div><span class="lbl">Last Changed:</span> '+fmtDate(u.password_last_set)+'</div>'+
    '<div><span class="lbl">Expires:</span> '+fmtDate(u.password_expiry)+'</div>'+
    '<div><span class="lbl">Days Left:</span> <span class="days-badge '+daysCls(u.days_until_expiry)+'">'+fmtRel(u.days_until_expiry)+'</span></div>'+
    '<div><span class="lbl">Locked:</span> '+(u.is_locked?"Yes":"No")+'</div>'+
    '<div><span class="lbl">Disabled:</span> '+(u.is_disabled?"Yes":"No")+'</div>'+
    '<div><span class="lbl">Must Change:</span> '+(u.must_change_at_logon?"Yes":"No")+'</div>'+
    '<div><span class="lbl">Status Reason:</span> '+esc(reasonLabel(u.status_reason))+'</div>'+
    '<div><span class="lbl">Latest Email:</span> <span class="pill '+emailMeta.cls+'">'+emailMeta.label+'</span> '+esc(u.last_email_template || "-")+' #'+esc(u.last_email_attempt || "-")+'</div>'+
    '</div><h4 style="margin-top:20px">Email Delivery History</h4>'+
    '<div class="table-wrap"><table class="tbl"><thead><tr><th>Time</th><th>Template</th><th>Attempt</th><th>Status</th><th>Error</th></tr></thead><tbody>'+
    buildEmailHistoryRows(emailHist)+
    '</tbody></table></div>'+
    '<h4 style="margin-top:20px">Policy History</h4>'+
    '<div class="table-wrap"><table class="tbl"><thead><tr><th>Time</th><th>Action</th><th>Days</th><th>Run</th></tr></thead><tbody>'+
    buildPolicyHistoryRows(hist)+
    '</tbody></table></div>';
}

function buildUserDetailFooter(u) {
  return '<button class="btn btn-ghost" onclick="qa(\''+u.sam+'\',\'notify\')">Notify</button>'+
    '<button class="btn btn-warn" onclick="qa(\''+u.sam+'\',\'force\')">Force Change</button>'+
    '<button class="btn btn-primary" onclick="openPasswordDialog(\''+u.sam+'\')">Change Password</button>'+
    (u.is_disabled
      ? '<button class="btn btn-info" onclick="qa(\''+u.sam+'\',\'enable\')">Enable</button>'
      : (u.is_locked
        ? '<button class="btn btn-info" onclick="qa(\''+u.sam+'\',\'unlock\')">Unlock</button>'
        : '<button class="btn btn-danger" onclick="qa(\''+u.sam+'\',\'disable\')">Disable</button>'))+
    '<button class="btn btn-primary" onclick="closeModal()">Close</button>';
}

function toast(msg, type) {
  type = type || "info";
  const c = document.getElementById("toastContainer");
  const t = document.createElement("div");
  t.className = "toast toast-"+type;
  t.innerHTML = "<span>"+msg+"</span><button class='btn-icon'>X</button>";
  t.querySelector("button").addEventListener("click", ()=>t.remove());
  c.appendChild(t);
  setTimeout(()=>t.classList.add("show"), 10);
  setTimeout(()=>{t.classList.remove("show"); setTimeout(()=>t.remove(),300);}, 3500);
}

async function loadStats() {
  try {
    const d = await fetchJSON("/api/stats/kpi");
    document.getElementById("statAll").textContent = d.total_users;
    document.getElementById("statCompliant").textContent = d.compliant != null ? d.compliant : 0;
    document.getElementById("statExpiring").textContent = d.expiring_7d;
    document.getElementById("statExpired").textContent = d.expired;
    document.getElementById("statLocked").textContent = d.locked;
    document.getElementById("statDisabled").textContent = d.disabled || 0;
  } catch(e) { console.error(e); }
}

async function loadUsers() {
  const tbody = document.getElementById("usersTbody");
  tbody.innerHTML = '<tr><td colspan="9" class="muted"><div class="loading-state"><div class="spinner"></div></div></td></tr>';
  try {
    const url = new URL("/api/users", location.origin);
    url.searchParams.set("q", state.q);
    url.searchParams.set("status", state.status);
    url.searchParams.set("page", state.page);
    url.searchParams.set("size", state.size);
    url.searchParams.set("sort_by", state.sortBy);
    url.searchParams.set("sort_dir", state.sortDir);
    const data = await fetchJSON(url);
    state.users = data.users; state.total = data.total;
    renderTable(); renderPagi();
  } catch(e) { tbody.innerHTML = '<tr><td colspan="9" class="muted">Error: '+e.message+'</td></tr>'; }
}

function renderTable() {
  const tbody = document.getElementById("usersTbody");
  if (!state.users.length) { tbody.innerHTML = '<tr><td colspan="9" class="muted">No users found</td></tr>'; return; }
  tbody.innerHTML = state.users.map(buildUserRow).join("");
  document.querySelectorAll(".chk-user").forEach(cb => {
    cb.addEventListener("change", e => {
      const s = e.target.dataset.sam;
      if (e.target.checked) state.selected.add(s); else state.selected.delete(s);
      updateBulk();
    });
  });
  updateChkAll();
}

function renderPagi() {
  const tp = totalPages();
  const from = state.total===0?0:(state.page-1)*state.size+1;
  const to = Math.min(state.page*state.size, state.total);
  document.getElementById("pagiFrom").textContent = from;
  document.getElementById("pagiTo").textContent = to;
  document.getElementById("pagiTotal").textContent = state.total;
  document.getElementById("btnFirst").disabled = state.page===1;
  document.getElementById("btnPrev").disabled = state.page===1;
  document.getElementById("btnNext").disabled = state.page>=tp;
  document.getElementById("btnLast").disabled = state.page>=tp;
  const p = document.getElementById("pagiPages"); p.innerHTML = "";
  const start = Math.max(1, state.page-2), end = Math.min(tp, start+4);
  for (let i=start; i<=end; i++) {
    const b = document.createElement("button");
    b.className = "pagi-num" + (i===state.page?" active":""); b.textContent = i;
    b.addEventListener("click", ()=>{state.page=i; loadUsers();});
    p.appendChild(b);
  }
}
function updateBulk() {
  const bar = document.getElementById("bulkBar");
  document.getElementById("selCount").textContent = state.selected.size;
  bar.style.display = state.selected.size>0?"flex":"none";
}
function updateChkAll() {
  const c = document.getElementById("chkAll");
  const v = state.users.map(u=>u.sam);
  c.checked = v.length>0 && v.every(s=>state.selected.has(s));
  c.indeterminate = v.some(s=>state.selected.has(s)) && !c.checked;
}
async function qa(sam, action) {
  const label = {
    notify: "send a reminder email",
    force: "mark the account for force change",
    disable: "disable the account",
    enable: "enable the account",
    unlock: "unlock the account"
  }[action] || action;
  if (!confirm("Are you sure you want to " + label + " for " + sam + "?")) return;
  try {
    const result = await fetchJSON("/api/users/action", {method:"POST", body: JSON.stringify({sam, action})});
    const warn = result.refresh_warning ? " (snapshot refresh pending)" : "";
    toast("Action completed: "+label+" for "+sam+warn, "success");
    loadUsers(); loadStats();
  } catch(e) { toast("Error: "+e.message, "error"); }
}
window.qa = qa;
async function bulk(action) {
  const sams = Array.from(state.selected);
  if (!sams.length) return;
  const label = {
    notify: "send a reminder email",
    force: "mark the account for force change",
    disable: "disable the account",
    enable: "enable the account",
    unlock: "unlock the account"
  }[action] || action;
  if (!confirm("Are you sure you want to " + label + " for " + sams.length + " users?")) return;
  try {
    const r = await fetchJSON("/api/users/bulk-action", {method:"POST", body:JSON.stringify({sams,action})});
    const warningText = r.warnings ? " with " + r.warnings + " snapshot refresh warning(s)" : "";
    toast("Processed " + r.affected + " user(s)" + warningText, "success");
    state.selected.clear(); updateBulk(); loadUsers();
  } catch(e) { toast("Error: "+e.message, "error"); }
}
async function unlockExpiredUsers() {
  if (!confirm("Unlock all expired accounts that are currently locked?")) return;
  try {
    await fetchJSON("/api/control/unlock-expired", {method:"POST"});
    toast("Unlock process for expired locked accounts has started. Check Live Logs for progress.", "success");
  } catch(e) {
    toast("Error: "+e.message, "error");
  }
}
async function openModal(sam) {
  const bd = document.getElementById("modalBackdrop");
  const body = document.getElementById("modalBody");
  const foot = document.getElementById("modalFoot");
  bd.style.display = "flex";
  body.innerHTML = '<div class="loading-state"><div class="spinner"></div></div>';
  try {
    const u = await fetchJSON("/api/users/"+encodeURIComponent(sam));
    const hist = await fetchJSON("/api/users/"+encodeURIComponent(sam)+"/history");
    const emailHist = await fetchJSON("/api/users/"+encodeURIComponent(sam)+"/email-history");
    document.getElementById("modalTitle").textContent = u.display_name || u.sam;
    body.innerHTML = buildUserDetailBody(u, hist, emailHist);
    foot.innerHTML = buildUserDetailFooter(u);
  } catch(e) { body.innerHTML = '<div class="muted">Error: '+e.message+'</div>'; }
}
window.openModal = openModal;
function closeModal() { document.getElementById("modalBackdrop").style.display = "none"; }
window.closeModal = closeModal;

function setPasswordFieldVisibility(show) {
  const type = show ? "text" : "password";
  const pass = document.getElementById("adminNewPassword");
  const confirm = document.getElementById("adminConfirmPassword");
  const toggle = document.getElementById("btnTogglePassword");
  if (pass) pass.type = type;
  if (confirm) confirm.type = type;
  if (toggle) toggle.textContent = show ? "Hide Password" : "Show Password";
}

function focusAndSelectPasswordField(value) {
  const pass = document.getElementById("adminNewPassword");
  if (!pass) return;
  pass.focus();
  pass.select();
  try { pass.setSelectionRange(0, (value || pass.value || "").length); } catch (_) {}
}

function buildPasswordDialogBody(displayName, sam) {
  return '<div class="password-panel">'+
    '<p class="muted" style="text-align:left;padding:0;margin:0 0 16px 0;">Set a manual password for <strong>'+esc(displayName || sam)+'</strong> ('+esc(sam)+'). This feature is intended for IT/helpdesk use.</p>'+
    '<label class="field-label" for="adminNewPassword">New Password</label>'+
    '<input id="adminNewPassword" class="search-input password-input" type="password" placeholder="Enter the new password">'+
    '<div class="password-tools">'+
      '<button type="button" class="btn btn-info btn-sm" id="btnGeneratePassword">Generate Random</button>'+
      '<button type="button" class="btn btn-ghost btn-sm" id="btnTogglePassword">Show Password</button>'+
      '<button type="button" class="btn btn-ghost btn-sm" id="btnCopyPassword">Copy Password</button>'+
    '</div>'+
    '<label class="field-label" for="adminConfirmPassword">Confirm Password</label>'+
    '<input id="adminConfirmPassword" class="search-input password-input" type="password" placeholder="Repeat the new password">'+
    '<label class="check-row"><input id="adminMustChange" type="checkbox" checked> Force the user to change the password at next logon</label>'+
    '<label class="check-row"><input id="adminUnlockUser" type="checkbox"> Also unlock the account if it is currently locked</label>'+
    '<p class="muted" style="text-align:left;padding:0;margin:10px 0 0 0;">Note: the new password is not stored in the dashboard. After a successful reset, the user should sign in again to email, Global Secure Access, and work applications.</p>'+
    '<p class="muted password-hint" style="text-align:left;padding:0;margin:0;">The generator includes at least 1 uppercase letter, 1 lowercase letter, 1 number, and 1 symbol for stronger IT support usage.</p>'+
  '</div>';
}

function buildPasswordDialogFooter(sam) {
  return '<button class="btn btn-ghost" onclick="openModal(\''+sam+'\')">Back</button>'+
    '<button class="btn btn-primary" onclick="submitPasswordReset(\''+sam+'\')">Save Password</button>';
}

function populateGeneratedPassword() {
  const generated = generateStrongPassword(18);
  const pass = document.getElementById("adminNewPassword");
  const confirm = document.getElementById("adminConfirmPassword");
  if (pass) {
    pass.value = generated;
    focusAndSelectPasswordField(generated);
  }
  if (confirm) confirm.value = generated;
  toast("A strong random password has been generated", "success");
}

async function handleCopyPassword() {
  const value = document.getElementById("adminNewPassword")?.value || "";
  if (!value) {
    toast("Generate or enter a password first", "error");
    return;
  }
  try {
    const copyMode = await copyTextWithFallback(value);
    if (copyMode === "clipboard") {
      toast("Password copied", "success");
      return;
    }
    if (copyMode === "prompt") {
      toast("Clipboard blocked by browser. Manual copy dialog opened.", "warn");
      return;
    }
    throw new Error("copy failed");
  } catch (_) {
    focusAndSelectPasswordField(value);
    toast("Clipboard access failed. Password selected for manual copy.", "error");
  }
}

function bindPasswordDialogEvents() {
  document.getElementById("btnGeneratePassword")?.addEventListener("click", populateGeneratedPassword);
  document.getElementById("btnTogglePassword")?.addEventListener("click", () => {
    const pass = document.getElementById("adminNewPassword");
    const isShown = pass?.type === "text";
    setPasswordFieldVisibility(!isShown);
  });
  document.getElementById("btnCopyPassword")?.addEventListener("click", handleCopyPassword);
}

function openPasswordDialog(sam) {
  const user = state.users.find(u => u.sam === sam);
  const displayName = (user && (user.display_name || user.sam)) || sam;
  const bd = document.getElementById("modalBackdrop");
  const body = document.getElementById("modalBody");
  const foot = document.getElementById("modalFoot");
  bd.style.display = "flex";
  document.getElementById("modalTitle").textContent = "Change Password";
  body.innerHTML = buildPasswordDialogBody(displayName, sam);
  foot.innerHTML = buildPasswordDialogFooter(sam);
  bindPasswordDialogEvents();
}
window.openPasswordDialog = openPasswordDialog;

async function submitPasswordReset(sam) {
  const newPassword = document.getElementById("adminNewPassword")?.value || "";
  const confirmPassword = document.getElementById("adminConfirmPassword")?.value || "";
  const mustChange = !!document.getElementById("adminMustChange")?.checked;
  const unlockUser = !!document.getElementById("adminUnlockUser")?.checked;
  if (newPassword.length < 8) {
    toast("Password must be at least 8 characters", "error");
    return;
  }
  if (newPassword !== confirmPassword) {
    toast("Password confirmation does not match", "error");
    return;
  }
  if (!confirm("Are you sure you want to set a new password for " + sam + "?")) return;
  try {
    const result = await fetchJSON("/api/users/" + encodeURIComponent(sam) + "/set-password", {
      method: "POST",
      body: JSON.stringify({
        new_password: newPassword,
        must_change: mustChange,
        unlock_user: unlockUser
      })
    });
    toast("Password changed for " + (result.display_name || sam), "success");
    closeModal();
    loadUsers();
    loadStats();
  } catch (e) {
    const raw = String(e.message || "");
    if (raw.includes("LDAPS") || raw.includes("port 636") || raw.includes("modify_password")) {
      toast("Password reset is blocked because LDAPS on the domain controller is not ready yet.", "error");
    } else {
      toast("Error: " + raw, "error");
    }
  }
}
window.submitPasswordReset = submitPasswordReset;

const debSearch = debounce(()=>{state.page=1; loadUsers();}, 350);
document.getElementById("userSearch").addEventListener("input", e => {
  state.q = e.target.value.trim();
  document.getElementById("btnClearSearch").style.display = state.q?"flex":"none";
  debSearch();
});
document.getElementById("btnClearSearch").addEventListener("click", () => {
  document.getElementById("userSearch").value = ""; state.q = "";
  document.getElementById("btnClearSearch").style.display = "none";
  state.page = 1; loadUsers();
});
document.querySelectorAll(".pill-btn").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".pill-btn").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    state.status = b.dataset.status; state.page = 1; loadUsers();
  });
});
document.querySelectorAll(".stat-item[data-filter]").forEach(el => {
  el.addEventListener("click", () => {
    const s = el.dataset.filter;
    document.querySelectorAll(".pill-btn").forEach(b => b.classList.toggle("active", b.dataset.status===s));
    state.status = s; state.page = 1; loadUsers();
  });
});
document.querySelectorAll("th.sortable").forEach(th => {
  th.addEventListener("click", () => {
    const c = th.dataset.sort;
    if (state.sortBy === c) state.sortDir = state.sortDir==="asc"?"desc":"asc";
    else { state.sortBy = c; state.sortDir = "asc"; }
    document.querySelectorAll("th.sortable").forEach(h=>h.classList.remove("sort-asc","sort-desc"));
    th.classList.add(state.sortDir==="asc"?"sort-asc":"sort-desc");
    state.page = 1; loadUsers();
  });
});
document.getElementById("chkAll").addEventListener("change", e => {
  state.users.forEach(u => { if (e.target.checked) state.selected.add(u.sam); else state.selected.delete(u.sam); });
  renderTable(); updateBulk();
});
document.getElementById("btnClearSel").addEventListener("click", () => {
  state.selected.clear(); renderTable(); updateBulk();
});
document.getElementById("btnBulkNotify").addEventListener("click", ()=>bulk("notify"));
document.getElementById("btnBulkForce").addEventListener("click", ()=>bulk("force"));
document.getElementById("btnBulkUnlock").addEventListener("click", ()=>bulk("unlock"));
document.getElementById("btnBulkEnable").addEventListener("click", ()=>bulk("enable"));
document.getElementById("btnBulkDisable").addEventListener("click", ()=>bulk("disable"));
document.getElementById("btnUnlockExpired").addEventListener("click", unlockExpiredUsers);
document.getElementById("btnRefresh").addEventListener("click", ()=>{loadStats(); loadUsers();});
document.getElementById("pageSize").addEventListener("change", e => { state.size = parseInt(e.target.value); state.page = 1; loadUsers(); });
document.getElementById("btnFirst").addEventListener("click", ()=>{state.page=1; loadUsers();});
document.getElementById("btnPrev").addEventListener("click", ()=>{if(state.page>1){state.page--;loadUsers();}});
document.getElementById("btnNext").addEventListener("click", ()=>{if(state.page < totalPages()){state.page++; loadUsers();}});
document.getElementById("btnLast").addEventListener("click", ()=>{state.page=totalPages(); loadUsers();});
document.getElementById("btnCloseModal").addEventListener("click", closeModal);
document.getElementById("modalBackdrop").addEventListener("click", e => {if(e.target.id==="modalBackdrop") closeModal();});
document.addEventListener("keydown", e => {if(e.key==="Escape") closeModal();});
document.getElementById("btnExportCsv").addEventListener("click", async () => {
  const url = new URL("/api/users/export", location.origin);
  url.searchParams.set("q", state.q); url.searchParams.set("status", state.status);
  try {
    const r = await fetch(url, {headers: H()});
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
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "pce-users-"+new Date().toISOString().slice(0,10)+".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  } catch (e) {
    toast("Export failed: " + e.message, "error");
  }
});
document.addEventListener("sse:run_completed", () => { loadStats(); loadUsers(); });
loadStats(); loadUsers();
