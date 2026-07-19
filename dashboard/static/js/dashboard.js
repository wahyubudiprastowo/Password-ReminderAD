const H = () => window.getPceHeaders({ "Content-Type": "application/json" });

async function fetchJSON(url) {
  const r = await fetch(url, { headers: H() });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

async function loadKPI() {
  try {
    const d = await fetchJSON("/api/stats/kpi");
    const r = d.latest_run || {};
    document.getElementById("kpiCompliant").textContent = d.compliant != null ? d.compliant : 0;
    document.getElementById("kpiExpiring").textContent = d.expiring_7d;
    document.getElementById("kpiExpired").textContent = d.expired;
    document.getElementById("kpiLocked").textContent = d.locked;
    document.getElementById("kpiTotal").textContent = d.total_users;
    document.getElementById("kpiLastRun").textContent = r.started_at
      ? window.formatDateTime(r.started_at, { year: undefined, second: undefined })
      : "N/A";
    const schedule = d.schedule || {};
    document.getElementById("schedulerFrequency").textContent = schedule.frequency_label || "-";
    document.getElementById("schedulerNextRun").textContent = window.formatDateTime(schedule.next_run_at) || "-";
    document.getElementById("schedulerTimezone").textContent = schedule.timezone || "-";
    document.getElementById("schedulerCron").textContent = schedule.cron || "-";
    document.getElementById("schedulerThresholdNote").textContent = schedule.threshold_note || "-";
  } catch (e) { console.error(e); }
}

let trendChart, distChart;
async function loadCharts() {
  const trend = await fetchJSON("/api/stats/trend?days=30");
  const opts = {
    chart: { type:"area", height: 300, toolbar:{show:false}, foreColor:"#a8b0c8", background:"transparent" },
    theme: { mode: document.documentElement.dataset.theme },
    colors: ["#f59e0b","#8b5cf6","#ef4444"],
    dataLabels: { enabled:false },
    stroke: { curve:"smooth", width: 2 },
    fill: { type:"gradient", gradient:{ shadeIntensity:1, opacityFrom: .5, opacityTo: .05 } },
    xaxis: { categories: trend.map(t => window.formatDateOnly(t.started_at, { year: undefined })) },
    grid: { borderColor: "rgba(255,255,255,0.08)" },
    legend: { position:"top" },
    series: [
      { name:"Warned", data: trend.map(t=>t.warned) },
      { name:"Forced", data: trend.map(t=>t.forced_change) },
      { name:"Disabled", data: trend.map(t=>t.disabled) }
    ]
  };
  if (trendChart) trendChart.destroy();
  trendChart = new ApexCharts(document.getElementById("trendChart"), opts);
  trendChart.render();

  const dist = await fetchJSON("/api/stats/distribution");
  const distOpts = {
    chart: { type:"donut", height: 300, foreColor:"#a8b0c8", background:"transparent" },
    theme: { mode: document.documentElement.dataset.theme },
    labels: Object.keys(dist),
    series: Object.values(dist),
    colors: ["#ef4444","#f59e0b","#eab308","#3b82f6","#22c55e","#6366f1"],
    legend: { position:"bottom" },
    plotOptions: { pie: { donut: { size: "70%" } } },
    dataLabels: { enabled: true }
  };
  if (distChart) distChart.destroy();
  distChart = new ApexCharts(document.getElementById("distChart"), distOpts);
  distChart.render();
}

async function loadActions() {
  try {
    const acts = await fetchJSON("/api/actions?limit=25");
    const tbody = document.getElementById("actionsTbody");
    if (!acts.length) { tbody.innerHTML = '<tr><td colspan="6" class="muted">No actions</td></tr>'; return; }
    tbody.innerHTML = acts.map(a => "<tr>"+
      "<td><strong>"+(a.sam||"-")+"</strong></td>"+
      "<td><span class='pill "+(a.action==="Disabled"?"pill-danger":a.action==="Warned"?"pill-warn":"pill-good")+"'>"+a.action+"</span></td>"+
      "<td>"+a.days_left+"</td>"+
      "<td class='muted'>"+(a.email||"-")+"</td>"+
      "<td>"+(a.email_status ? ("<span class='pill "+(a.email_status==="sent"?"pill-good":a.email_status==="failed"?"pill-danger":"pill-neutral")+"'>"+a.email_status.toUpperCase()+"</span> <span class='muted'>#"+(a.email_attempt||1)+" "+(a.email_template||"")+"</span>") : "<span class='muted'>Not sent</span>")+"</td>"+
      "<td class='muted'>"+window.formatDateTime(a.timestamp)+"</td>"+
    "</tr>").join("");
  } catch (e) { console.error(e); }
}

document.addEventListener("sse:run_completed", () => { loadKPI(); loadActions(); loadCharts(); });
loadKPI(); loadCharts(); loadActions();
setInterval(loadKPI, 30000);
