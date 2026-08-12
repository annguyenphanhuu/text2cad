/**
 * Tolery CAD Monitor — JS v2
 * State-preserving refresh, chart expand, panel collapse, resize, tooltips, pause.
 */
(() => {
  "use strict";

  // ─── Utils ───────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const getToken = () => localStorage.getItem("api-token") || "";
  const fmtMb  = (v) => (v == null ? "–" : `${Number(v).toFixed(0)} MB`);
  const fmtGb  = (v) => (v == null ? "–" : `${Number(v).toFixed(2)} GB`);
  const fmtCost= (v) => (v == null ? "–" : `$${Number(v).toFixed(6)}`);
  const fmtPct = (v) => (v == null ? "–" : `${Number(v).toFixed(1)}%`);
  const fmtNum = (v) => (v == null ? "–" : Number(v).toLocaleString());
  const fmtPriority = (v) => (v == null ? "P–" : `P${Number(v)}`);
  const shortId= (s, n=20) => (!s ? "–" : s.length<=n ? s : s.slice(0,n)+"…");

  const fmtMs = (ms) => {
    if (ms == null) return "–";
    const n = Number(ms);
    if (n >= 60000) return `${(n/60000).toFixed(1)} min`;
    if (n >= 1000)  return `${(n/1000).toFixed(1)} s`;
    return `${Math.round(n)} ms`;
  };
  const fmtTime = (iso) => {
    if (!iso) return "";
    try { const d = new Date(iso); return `${d.getHours().toString().padStart(2,"0")}:${d.getMinutes().toString().padStart(2,"0")}:${d.getSeconds().toString().padStart(2,"0")}`; }
    catch { return iso; }
  };
  const numOrZero = (v) => Number(v == null ? 0 : v);
  const pickServerRam = (s) => numOrZero(s.server_ram_used_mb ?? s.system_ram_used_mb ?? s.process_rss_mb);
  const pickServerCpu = (s) => numOrZero(s.server_cpu_percent ?? s.system_cpu_percent ?? s.process_cpu_percent);
  const movingAverage = (values, windowSize) => values.map((_, idx) => {
    const start = Math.max(0, idx - windowSize + 1);
    const slice = values.slice(start, idx + 1);
    return slice.reduce((sum, v) => sum + Number(v || 0), 0) / slice.length;
  });
  const updateResourceReadout = (sample, cpuAvg = null) => {
    const el = $("resource-readout");
    if (!el || !sample) return;
    el.textContent =
      `${fmtTime(sample.timestamp)} | RAM ${fmtMb(pickServerRam(sample))} (${fmtPct(sample.system_ram_percent)}) | ` +
      `CPU ${fmtPct(pickServerCpu(sample))}${cpuAvg == null ? "" : ` / avg ${fmtPct(cpuAvg)}`} | ` +
      `API ${fmtMb(sample.api_process_rss_mb)} / ${fmtPct(sample.api_process_cpu_percent)}`;
  };

  const COLORS = ["#4d9ef5","#34d399","#f59e42","#a78bfa","#2dd4bf","#f87171","#fbbf24","#60a5fa"];

  // ─── State ───────────────────────────────────────────────
  const state = {
    // Charts
    ramChart: null, cpuChart: null, stepChart: null, tputChart: null,
    modalChart: null,
    // Throughput history
    tputHistory: [],
    // Refresh
    timer: null,
    paused: false,
    // UI state preservation
    openGroups: new Set(),    // session_id of expanded session groups
    scrollPositions: {},      // panel-id → scrollTop
    // Latest data cache
    lastPerf: null, lastSizing: null, lastObs: null,
    safetyFactor: 1.6,
    resourceWindowMinutes: 1,
  };

  // ─── API fetch ───────────────────────────────────────────
  async function fetchJson(url) {
    const token = getToken();
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ─── Scroll preservation ────────────────────────────────
  function saveScrolls() {
    ["body-resources","body-pipeline","body-perf"].forEach(id => {
      const el = $(id);
      if (el) state.scrollPositions[id] = el.scrollTop;
    });
  }
  function restoreScrolls() {
    ["body-resources","body-pipeline","body-perf"].forEach(id => {
      const el = $(id);
      if (el && state.scrollPositions[id] != null) el.scrollTop = state.scrollPositions[id];
    });
  }

  // ─── Token UI ────────────────────────────────────────────
  function setTokenStatus() {
    const token = getToken();
    const el = $("token-state");
    if (token) { el.textContent = `Token saved (…${token.slice(-8)})`; el.style.color = "var(--green)"; }
    else       { el.textContent = "No token — some APIs may return 401"; el.style.color = "var(--yellow)"; }
  }

  // ─── KPI flash ───────────────────────────────────────────
  function setKpi(id, value) {
    const e = $(id);
    if (e && e.textContent !== String(value)) {
      e.textContent = value;
      e.classList.add("flash");
      setTimeout(() => e.classList.remove("flash"), 350);
    }
  }

  // ─── Overview renderer ───────────────────────────────────
  function renderOverview(ov) {
    setKpi("kpi-active-users",    ov.active_user_count ?? 0);
    const serverCounts = ov.active_server_counts || ov.current_ram?.active_server_counts || {};
    const chatbotActive = serverCounts.chatbot_server ?? 0;
    const freecadActive = serverCounts.freecad_server ?? 0;
    setKpi("kpi-active-requests", `${chatbotActive} / ${freecadActive}`);
    const ram = ov.current_ram || {};
    setKpi("kpi-ram-val",  fmtMb(ram.server_ram_used_mb ?? ram.system_ram_used_mb ?? ram.process_rss_mb));
    setKpi("kpi-cpu-val",  fmtPct(ram.server_cpu_percent ?? ram.system_cpu_percent ?? ram.process_cpu_percent));
    setKpi("kpi-cost-val", fmtCost(ov.total_recorded_cost_usd ?? 0));
    $("completed-label").textContent = `${ov.completed_request_count ?? 0} completed`;

    // Stats
    const peak = ov.global_ram_peak || {};
    $("stat-ram-peak").textContent = fmtMb(peak.server_ram_used_mb ?? peak.system_ram_used_mb ?? peak.process_rss_mb);
    $("stat-cores").textContent     = ram.cpu_count ?? "–";
    const cpuPeak = ov.cpu_peak || {};
    $("stat-cpu-peak").textContent  = fmtPct(cpuPeak.server_cpu_percent ?? cpuPeak.system_cpu_percent ?? cpuPeak.process_cpu_percent);

    // Peak box
    if (peak.process_rss_mb > 0) {
      $("peak-summary").textContent = `${fmtMb(peak.process_rss_mb)} @ ${fmtTime(peak.timestamp)} — ${peak.active_request_count ?? 0} req active`;
      $("peak-steps").innerHTML = Object.entries(peak.active_steps || {})
        .map(([k,v]) => `<span class="chip">${k}: ${v}</span>`).join("");
    }

    renderStepDonut(ov.active_requests || []);
    renderActiveRequests(ov.active_requests || []);
    renderSessionGroups(ov.session_groups || []);
    renderSessions(ov.sessions || []);
  }

  // ─── Charts: RAM & CPU ───────────────────────────────────
  async function renderResourceCharts() {
    const limit = Math.max(90, Math.ceil(state.resourceWindowMinutes * 75));
    const data = await fetchJson(`/api/monitor/ram?limit=${limit}`);
    const allSamples = data.samples || [];
    const cutoff = Date.now() - state.resourceWindowMinutes * 60 * 1000;
    const samples = allSamples.filter(s => {
      const t = Date.parse(s.timestamp || "");
      return Number.isNaN(t) ? true : t >= cutoff;
    });
    const labels      = samples.map(s => fmtTime(s.timestamp));
    const rssData     = samples.map(pickServerRam);
    const sysRamData  = samples.map(s => s.system_ram_percent || 0);
    const cpuData     = samples.map(pickServerCpu);
    const cpuAvgData  = movingAverage(cpuData, 10);

    if (samples.length) updateResourceReadout(samples[samples.length - 1], cpuAvgData[cpuAvgData.length - 1]);

    const hoverOptions = {
      mode: "index",
      intersect: false,
      axis: "x",
    };
    const commonTooltip = {
      backgroundColor: "#101522",
      titleColor: "#e8eaf0",
      bodyColor: "#cbd5e1",
      borderColor: "#4d9ef5",
      borderWidth: 1,
      padding: 10,
      displayColors: true,
      callbacks: {
        title: (items) => {
          const idx = items?.[0]?.dataIndex ?? 0;
          return samples[idx] ? fmtTime(samples[idx].timestamp) : "";
        },
        afterBody: (items) => {
          const idx = items?.[0]?.dataIndex ?? 0;
          const s = samples[idx];
          if (!s) return [];
          return [
            `Server RAM: ${fmtMb(pickServerRam(s))} / ${fmtMb(s.system_ram_total_mb)} (${fmtPct(s.system_ram_percent)})`,
            `Server CPU: ${fmtPct(pickServerCpu(s))}`,
            `CPU avg 10s: ${fmtPct(cpuAvgData[idx])}`,
            `API RAM: ${fmtMb(s.api_process_rss_mb)}`,
            `API CPU: ${fmtPct(s.api_process_cpu_percent)}`,
            `Chatbot active: ${s.active_server_counts?.chatbot_server ?? 0}`,
            `FreeCAD active: ${s.active_server_counts?.freecad_server ?? 0}`,
          ];
        },
      },
    };
    const hoverPlugin = {
      id: "resourceHoverReadout",
      afterEvent(chart, args) {
        const event = args.event;
        if (!event || event.type !== "mousemove") return;
        const points = chart.getElementsAtEventForMode(event, "index", { intersect: false }, false);
        if (!points.length) return;
        updateResourceReadout(samples[points[0].index], cpuAvgData[points[0].index]);
      },
    };

    const chartCfg = {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: hoverOptions,
      elements: {
        point: { radius: 1.8, hoverRadius: 6, hitRadius: 10 },
        line: { borderWidth: 2 },
      },
      plugins: {
        legend: { display: true, labels: { boxWidth: 8, font: { size: 10 }, color: "#9aa3b8" } },
        tooltip: commonTooltip,
      },
      scales: {
        x: {
          ticks: { display: true, maxTicksLimit: 8, color:"#5c6480", font:{size:10}, maxRotation:0 },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
        y: { beginAtZero: false, grace:"8%", title: { display:true, text:"MB", color:"#5c6480", font:{size:10} }, ticks:{color:"#5c6480",font:{size:10}}, grid:{color:"rgba(255,255,255,0.04)"} },
        y1: { position:"right", min:0, max:100, grid:{drawOnChartArea:false}, title:{display:true,text:"%",color:"#5c6480",font:{size:10}}, ticks:{color:"#5c6480",font:{size:10}} },
      },
    };

    const ramDataset = {
      labels,
      datasets: [
        { label:"Server RAM Used MB", data:rssData, borderColor:"#4d9ef5", backgroundColor:"rgba(77,158,245,0.1)", tension:0.3, fill:true, yAxisID:"y", pointRadius:0, borderWidth:1.5 },
        { label:"Server RAM %",   data:sysRamData, borderColor:"#f87171", backgroundColor:"transparent", tension:0.3, yAxisID:"y1", pointRadius:0, borderWidth:1.5 },
      ],
    };

    if (!state.ramChart) {
      state.ramChart = new Chart($("ram-chart"), { type:"line", data:ramDataset, options:chartCfg, plugins:[hoverPlugin] });
    } else { state.ramChart.data = ramDataset; state.ramChart.update("none"); }

    const cpuDataset = {
      labels,
      datasets: [
        { label:"Server CPU %", data:cpuData, borderColor:"#f59e42", backgroundColor:"rgba(245,158,66,0.12)", tension:0.25, fill:true, pointRadius:0, borderWidth:1.4 },
        { label:"CPU avg 10s", data:cpuAvgData, borderColor:"#34d399", backgroundColor:"transparent", tension:0.35, fill:false, pointRadius:0, borderWidth:2.2 },
      ],
    };
    const cpuCfg = { responsive:true, maintainAspectRatio:false, animation:false,
      interaction: hoverOptions,
      elements:{ point:{ radius:1.8, hoverRadius:6, hitRadius:10 }, line:{ borderWidth:2 } },
      plugins:{ legend:{display:true, labels:{ boxWidth:8, font:{size:10}, color:"#9aa3b8" }}, tooltip:commonTooltip },
      scales:{
        x:{ticks:{display:true,maxTicksLimit:8,color:"#5c6480",font:{size:10},maxRotation:0},grid:{color:"rgba(255,255,255,0.04)"}},
        y:{min:0,max:100,title:{display:true,text:"CPU%",color:"#5c6480",font:{size:10}},ticks:{color:"#5c6480",font:{size:10}},grid:{color:"rgba(255,255,255,0.04)"}},
      },
    };
    if (!state.cpuChart) {
      state.cpuChart = new Chart($("cpu-chart"), { type:"line", data:cpuDataset, options:cpuCfg, plugins:[hoverPlugin] });
    } else { state.cpuChart.data = cpuDataset; state.cpuChart.update("none"); }
  }

  // ─── Step donut ──────────────────────────────────────────
  function renderStepDonut(activeRequests) {
    const counts = {};
    activeRequests.forEach(r => { const s = r.current_step||"unknown"; counts[s]=(counts[s]||0)+1; });
    let labels = Object.keys(counts), values = Object.values(counts);
    const idle = !labels.length;
    if (idle) { labels=["idle"]; values=[1]; }

    $("step-legend").innerHTML = idle
      ? `<div class="empty-hint">System idle</div>`
      : labels.map((l,i)=>`<div class="legend-item"><span class="legend-dot" style="background:${COLORS[i%COLORS.length]}"></span><span>${l}: <strong>${values[i]}</strong></span></div>`).join("");

    const chartData = { labels, datasets:[{ data:values, backgroundColor: idle?["rgba(92,100,128,0.3)"]:labels.map((_,i)=>COLORS[i%COLORS.length]), borderWidth:0, hoverOffset:4 }] };
    const opts = { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{backgroundColor:"#1a2035",titleColor:"#e8eaf0",bodyColor:"#9aa3b8"}}, cutout:"62%" };

    if (!state.stepChart) {
      state.stepChart = new Chart($("step-chart"), { type:"doughnut", data:chartData, options:opts });
    } else { state.stepChart.data = chartData; state.stepChart.update("none"); }
  }

  // ─── Active requests ─────────────────────────────────────
  function renderActiveRequests(requests) {
    const list = $("active-request-list");
    if (!requests.length) {
      list.innerHTML = `<div class="empty-state"><i class="fas fa-satellite-dish"></i><span>No active CAD streams</span></div>`;
      return;
    }
    list.innerHTML = requests.map(r => {
      const pct = Math.min(100, Number(r.overall_percentage||0));
      return `<div class="request-card">
        <div class="request-card-top">
          <span class="request-id">${shortId(r.request_id,22)}</span>
          <span class="request-step"><span class="priority-badge">${fmtPriority(r.priority)}</span> ${r.current_step||"–"} (${pct}%)</span>
        </div>
        <div class="request-meta">
          <span>User: ${shortId(r.user_id||"anon",18)}</span>
          <span>RAM pk: ${fmtMb(r.ram_peak_mb)}</span>
          <span>CPU pk: ${fmtPct(r.cpu_peak_percent)}</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
      </div>`;
    }).join("");
  }

  // ─── Session groups (STATE-PRESERVING) ───────────────────
  function renderSessionGroups(groups) {
    const container = $("session-groups");
    if (!groups.length) {
      container.innerHTML = `<div class="empty-state"><i class="fas fa-folder-open"></i><span>No session groups yet</span></div>`;
      return;
    }

    // Build new HTML but preserve open state
    const html = groups.slice(0,15).map((g) => {
      const sid = g.session_id || "";
      const isOpen = state.openGroups.has(sid);
      const status = g.status || "completed";
      const tracker = g.priority_tracker || {};
      const activePriority = tracker.highest_active ?? g.active_priority;
      const displayPriority = activePriority ?? tracker.highest_seen ?? g.last_priority ?? g.max_priority;
      const activePriorityCount = (tracker.active || []).length;

      const turns = (g.requests||[]).map((req, i) => {
        const steps = (req.steps||[]).slice(-6).map(st =>
          `<span class="turn-step ${st.is_complete?"ok":""}">${st.step_name||"?"} ${fmtMs(st.duration_ms)}</span>`
        ).join("");
        return `<div class="turn-row">
          <span class="turn-label">Turn ${i+1}</span>
          <span class="priority-badge">${fmtPriority(req.priority)}</span>
          <span class="turn-msg" title="${req.message_preview||""}">${req.message_preview||"–"}</span>
          <span class="turn-dur">${fmtMs(req.duration_ms)}</span>
          <span class="turn-cost">${fmtCost(req.total_cost_usd)}</span>
          ${steps ? `<div class="turn-steps">${steps}</div>` : ""}
        </div>`;
      }).join("");

      return `<div class="session-group ${isOpen?"open":""} ${status}" data-sid="${sid}">
        <div class="sg-header" onclick="window.__toggleGroup('${sid}')">
          <div>
            <div class="sg-id" title="${sid}">${shortId(sid,26)}</div>
            <div class="sg-user">${shortId(g.user_id||"anon",22)}</div>
          </div>
          <div class="sg-stat"><strong>${fmtMs(g.total_duration_ms)}</strong><span>Total time</span></div>
          <div class="sg-stat"><strong>${fmtCost(g.total_cost_usd)}</strong><span>Cost</span></div>
          <div class="sg-stat"><strong>${fmtMb(g.ram_peak_mb)}</strong><span>RAM pk</span></div>
          <div class="sg-stat priority-stat"><strong>${fmtPriority(displayPriority)}</strong><span>${activePriorityCount ? `${activePriorityCount} active` : "Priority"}</span></div>
          <span class="sg-status ${status}">${status}</span>
          <i class="fas fa-chevron-right sg-chevron"></i>
        </div>
        <div class="sg-turns">${turns||"<span style='color:var(--text3);font-size:11px;padding:6px 0;display:block'>No turns yet</span>"}</div>
      </div>`;
    }).join("");

    container.innerHTML = html;
  }

  // Toggle session group — updates state.openGroups
  window.__toggleGroup = (sid) => {
    if (state.openGroups.has(sid)) state.openGroups.delete(sid);
    else state.openGroups.add(sid);
    // Find element and toggle directly (no full re-render)
    const el = document.querySelector(`.session-group[data-sid="${sid}"]`);
    if (el) {
      el.classList.toggle("open", state.openGroups.has(sid));
      const chevron = el.querySelector(".sg-chevron");
      if (chevron) chevron.style.transform = state.openGroups.has(sid) ? "rotate(90deg)" : "";
    }
  };

  // ─── Sessions table ──────────────────────────────────────
  function renderSessions(sessions) {
    $("session-count-label").textContent = `${sessions.length} sessions`;
    $("total-tokens-label").textContent  = `${fmtNum(sessions.reduce((s,x)=>s+Number(x.total_tokens||0),0))} tokens`;
    const body = $("sessions-body");
    if (!sessions.length) { body.innerHTML = `<tr><td colspan="7" class="muted">No sessions yet</td></tr>`; return; }
    body.innerHTML = sessions.slice(0,16).map(s=>`<tr>
      <td title="${s.session_id||""}">${shortId(s.session_id,14)}</td>
      <td title="${s.user_id||""}">${shortId(s.user_id||"anon",10)}</td>
      <td>${s.request_count??0}</td>
      <td><span class="priority-badge">${fmtPriority(s.last_priority ?? s.max_priority)}</span></td>
      <td>${fmtMb(s.ram_peak_mb)}</td>
      <td>${fmtNum(s.total_tokens)}</td>
      <td>${fmtCost(s.total_cost_usd)}</td>
    </tr>`).join("");
  }

  // ─── Performance ─────────────────────────────────────────
  async function renderPerformance() {
    const perf = await fetchJson("/api/monitor/performance");
    state.lastPerf = perf;
    const lat = perf.latency_ms || {};
    setKpi("kpi-p95-val", fmtMs(lat.p95));
    $("lat-avg").textContent = fmtMs(lat.avg);
    $("lat-p50").textContent = fmtMs(lat.p50);
    $("lat-p90").textContent = fmtMs(lat.p90);
    $("lat-p95").textContent = fmtMs(lat.p95);
    $("lat-p99").textContent = fmtMs(lat.p99);
    $("lat-max").textContent = fmtMs(lat.max);
    $("lat-samples").textContent = `${perf.sample_count??0} completed request(s) sampled`;

    const tput = perf.throughput || {};
    $("tput-in").textContent  = Number(tput.req_per_min_in||0).toFixed(1);
    $("tput-out").textContent = Number(tput.req_per_min_out||0).toFixed(1);

    // Throughput history
    const now = new Date();
    const ts = `${now.getHours().toString().padStart(2,"0")}:${now.getMinutes().toString().padStart(2,"0")}:${now.getSeconds().toString().padStart(2,"0")}`;
    state.tputHistory.push({ label:ts, in:tput.req_per_min_in||0, out:tput.req_per_min_out||0 });
    if (state.tputHistory.length > 60) state.tputHistory.shift();
    renderTputChart();

    // Step latency table
    const steps = perf.step_latency || {};
    const entries = Object.entries(steps).sort((a,b)=>(b[1].avg_ms||0)-(a[1].avg_ms||0));
    const tbody = $("step-latency-body");
    tbody.innerHTML = entries.length
      ? entries.map(([step,s])=>`<tr>
          <td style="font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--blue)">${step}</td>
          <td>${fmtNum(s.count)}</td>
          <td>${fmtMs(s.avg_ms)}</td>
          <td style="color:var(--orange)">${fmtMs(s.p95_ms)}</td>
          <td style="color:var(--red)">${fmtMs(s.max_ms)}</td>
        </tr>`).join("")
      : `<tr><td colspan="5" class="muted">No step data — run a request first</td></tr>`;
  }

  // ─── Throughput chart ────────────────────────────────────
  function renderTputChart() {
    const labels  = state.tputHistory.map(x=>x.label);
    const inData  = state.tputHistory.map(x=>x.in);
    const outData = state.tputHistory.map(x=>x.out);
    const tputDataset = { labels, datasets:[
      { label:"in",  data:inData,  borderColor:"#2dd4bf", backgroundColor:"rgba(45,212,191,0.1)",  tension:0.3, fill:true, pointRadius:0, borderWidth:1.5 },
      { label:"done",data:outData, borderColor:"#a78bfa", backgroundColor:"rgba(167,139,250,0.08)", tension:0.3, fill:true, pointRadius:0, borderWidth:1.5 },
    ]};
    const opts = { responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{legend:{display:true,labels:{boxWidth:8,font:{size:10},color:"#9aa3b8"}},tooltip:{backgroundColor:"#1a2035",titleColor:"#e8eaf0",bodyColor:"#9aa3b8"}},
      scales:{x:{ticks:{display:false},grid:{color:"rgba(255,255,255,0.04)"}},y:{beginAtZero:true,ticks:{color:"#5c6480",font:{size:10}},grid:{color:"rgba(255,255,255,0.04)"}}}
    };
    if (!state.tputChart) {
      state.tputChart = new Chart($("tput-chart"), { type:"line", data:tputDataset, options:opts });
    } else { state.tputChart.data = tputDataset; state.tputChart.update("none"); }
  }

  // ─── Sizing + Formula ────────────────────────────────────
  async function renderSizing() {
    const sf = state.safetyFactor;
    const sizing = await fetchJson(`/api/monitor/sizing?ram_safety_factor=${sf}`);
    state.lastSizing = sizing;
    state.lastObs    = sizing.observed || {};

    const conf = sizing.confidence || "low";
    const badge = $("sizing-confidence");
    badge.textContent = `Confidence: ${conf}`;
    badge.className = `confidence-badge ${conf}`;

    const rec = sizing.recommendation || {};
    $("sz-workers").textContent   = rec.workers ?? "–";
    $("sz-cores").textContent     = rec.cpu_cores ?? "–";
    $("sz-ram-worker").textContent= rec.ram_per_worker_gb != null ? fmtGb(rec.ram_per_worker_gb) : "–";
    $("sz-ram-total").textContent = rec.total_ram_gb != null ? fmtGb(rec.total_ram_gb) : "–";

    $("sizing-warnings").innerHTML = (sizing.warnings||[]).map(w=>
      `<div class="sizing-warn"><i class="fas fa-triangle-exclamation"></i><span>${w}</span></div>`).join("");
    $("sizing-notes").innerHTML = (sizing.notes||[]).map(n=>
      `<div class="sizing-note"><i class="fas fa-circle-info" style="margin-right:4px"></i>${n}</div>`).join("");

    // Update formula display if open
    if ($("formula-box").style.display !== "none") updateFormula(sizing, sf);
  }

  function updateFormula(sizing, sf) {
    const obs = sizing.observed || {};
    const rec = sizing.recommendation || {};
    const peakRss = obs.peak_rss_mb || 0;
    const ramWorker = rec.ram_per_worker_mb || 0;
    const peakConc = obs.peak_concurrent_requests || 0;
    const workers = rec.workers || 1;
    const totalRam = rec.total_ram_mb || 0;

    $("formula-steps").innerHTML = `
<span class="fline"><span class="fop">peak RSS (psutil)  =</span> <span class="fval">${peakRss.toFixed(0)} MB</span>  <span class="fop">← actual memory used by the process</span></span>
<span class="fline"><span class="fop">RAM / worker       =</span> <span class="fval">${peakRss.toFixed(0)}</span> <span class="fop">×</span> <span class="fval">${sf.toFixed(1)}</span> <span class="fop">(safety)</span> <span class="fop">=</span> <span class="fresult">${ramWorker} MB</span></span>
<span class="fline"><span class="fop">peak concurrent    =</span> <span class="fval">${peakConc}</span> <span class="fop">concurrent request(s)</span></span>
<span class="fline"><span class="fop">workers needed     =</span> <span class="fop">⌈</span><span class="fval">${peakConc}</span><span class="fop"> ÷ 0.70⌉ =</span> <span class="fresult">${workers} workers</span></span>
<span class="fline"><span class="fop">total RAM          =</span> <span class="fval">${ramWorker}</span><span class="fop"> × </span><span class="fval">${workers}</span><span class="fop"> + 512 MB overhead =</span> <span class="fresult">${totalRam} MB ≈ ${(totalRam/1024).toFixed(2)} GB</span></span>`;
  }

  // ─── Chart fullscreen modal ──────────────────────────────
  let modalChartRef = null;
  function openChartModal(sourceChartId, title) {
    const modal = $("chart-modal");
    $("chart-modal-title").textContent = title;
    modal.classList.add("open");

    // Destroy previous modal chart
    if (modalChartRef) { modalChartRef.destroy(); modalChartRef = null; }

    // Clone data from the source chart
    const sourceChart = {
      "ram-chart": state.ramChart,
      "cpu-chart": state.cpuChart,
      "tput-chart": state.tputChart,
    }[sourceChartId];

    if (!sourceChart) return;

    const canvas = $("chart-modal-canvas");
    // Reset canvas
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;

    const clonedData = JSON.parse(JSON.stringify(sourceChart.data));
    const clonedOpts = JSON.parse(JSON.stringify(sourceChart.options));
    clonedOpts.animation = false;
    clonedOpts.maintainAspectRatio = false;
    clonedOpts.interaction = { mode: "index", intersect: false, axis: "x" };
    clonedOpts.elements = clonedOpts.elements || {};
    clonedOpts.elements.point = { radius: 2.5, hoverRadius: 7, hitRadius: 12 };
    if (clonedOpts.scales) {
      Object.values(clonedOpts.scales).forEach(s => {
        if (s.ticks) s.ticks.display = true;
      });
    }

    modalChartRef = new Chart(canvas, { type: sourceChart.config.type, data: clonedData, options: clonedOpts });
  }

  function closeChartModal() {
    $("chart-modal").classList.remove("open");
    if (modalChartRef) { modalChartRef.destroy(); modalChartRef = null; }
  }

  // ─── Panel collapse ──────────────────────────────────────
  function initPanelCollapse() {
    document.querySelectorAll(".collapse-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const panelId = btn.dataset.target;
        const panel = $(panelId);
        if (panel) panel.classList.toggle("collapsed");
      });
    });
  }

  // ─── Column resize ───────────────────────────────────────
  function initResizeHandles() {
    const grid = $("dashboard-grid");

    function startResize(handle, colVar) {
      const isLeft = colVar === "left";
      let startX, startVal;

      const onMove = (e) => {
        const dx = e.clientX - startX;
        const newVal = Math.max(180, Math.min(500, startVal + (isLeft ? dx : -dx)));
        document.documentElement.style.setProperty(`--col-${colVar}`, `${newVal}px`);
        handle.classList.add("dragging");
      };
      const onUp = () => {
        handle.classList.remove("dragging");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      handle.addEventListener("mousedown", (e) => {
        startX = e.clientX;
        startVal = parseInt(getComputedStyle(document.documentElement).getPropertyValue(`--col-${colVar}`));
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
        e.preventDefault();
      });
    }

    const rhLeft  = $("rh-left");
    const rhRight = $("rh-right");
    if (rhLeft)  startResize(rhLeft,  "left");
    if (rhRight) startResize(rhRight, "right");
  }

  // ─── Tooltips ────────────────────────────────────────────
  function initTooltips() {
    const popup = $("tooltip-popup");
    document.querySelectorAll(".info-tip[data-tip]").forEach(tip => {
      tip.addEventListener("mouseenter", (e) => {
        popup.textContent = tip.dataset.tip;
        popup.classList.add("visible");
        positionTooltip(e);
      });
      tip.addEventListener("mousemove", positionTooltip);
      tip.addEventListener("mouseleave", () => popup.classList.remove("visible"));
    });
    function positionTooltip(e) {
      const pad = 12;
      let x = e.clientX + pad, y = e.clientY + pad;
      if (x + 270 > window.innerWidth) x = e.clientX - 270 - pad;
      if (y + 80 > window.innerHeight) y = e.clientY - 80 - pad;
      popup.style.left = x + "px";
      popup.style.top  = y + "px";
    }
  }

  // ─── Pause / resume ──────────────────────────────────────
  function togglePause() {
    state.paused = !state.paused;
    const btn = $("btn-pause");
    const dot = $("pulse-dot");
    const lbl = $("paused-label");
    if (state.paused) {
      btn.innerHTML = `<i class="fas fa-play"></i><span>Resume</span>`;
      btn.classList.add("paused");
      dot.className = "pulse-dot paused";
      lbl.style.display = "";
    } else {
      btn.innerHTML = `<i class="fas fa-pause"></i><span>Pause</span>`;
      btn.classList.remove("paused");
      dot.className = "pulse-dot";
      lbl.style.display = "none";
      refresh();
    }
  }

  // ─── Keyboard shortcuts ──────────────────────────────────
  function initKeyboard() {
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT") return;
      if (e.key === "p" || e.key === "P") togglePause();
      if (e.key === "r" || e.key === "R") refresh();
      if (e.key === "Escape") closeChartModal();
    });
  }

  // ─── Export ──────────────────────────────────────────────
  function doExport() {
    const token = getToken();
    const q = token ? `?token=${encodeURIComponent(token)}` : "";
    window.open(`/api/monitor/export${q}`, "_blank");
  }

  // ─── Main refresh ────────────────────────────────────────
  async function refresh() {
    if (state.paused) return;
    const dot = $("pulse-dot");
    saveScrolls();
    try {
      setTokenStatus();
      const overview = await fetchJson("/api/monitor/overview");
      renderOverview(overview);
      await renderResourceCharts();
      await renderPerformance();
      await renderSizing();
      restoreScrolls();
      $("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
      dot.className = "pulse-dot";
    } catch (err) {
      $("last-updated").textContent = `Error: ${err.message}`;
      dot.className = "pulse-dot error";
      if (String(err.message).includes("401")) {
        $("token-state").textContent = "Token missing / invalid";
        $("token-state").style.color = "var(--red)";
      }
    }
  }

  // ─── Boot ────────────────────────────────────────────────
  function boot() {
    setTokenStatus();

    $("save-token").addEventListener("click", () => {
      const t = $("monitor-token").value.trim();
      if (t) localStorage.setItem("api-token", t);
      else   localStorage.removeItem("api-token");
      $("monitor-token").value = "";
      setTokenStatus(); refresh();
    });
    $("clear-token").addEventListener("click", () => {
      localStorage.removeItem("api-token");
      $("monitor-token").value = "";
      setTokenStatus();
    });
    $("btn-refresh").addEventListener("click", () => {
      const icon = $("btn-refresh").querySelector("i");
      icon.style.transition = "transform 0.5s"; icon.style.transform = "rotate(360deg)";
      setTimeout(() => { icon.style.transform=""; }, 520);
      refresh();
    });
    $("btn-pause").addEventListener("click", togglePause);
    $("btn-export").addEventListener("click", doExport);

    // Chart fullscreen
    document.querySelectorAll(".chart-range").forEach(btn => {
      btn.addEventListener("click", () => {
        state.resourceWindowMinutes = Number(btn.dataset.minutes || 1);
        document.querySelectorAll(".chart-range").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        renderResourceCharts();
      });
    });
    document.querySelectorAll(".expand-chart-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openChartModal(btn.dataset.chart, btn.dataset.title || "Chart");
      });
    });
    $("chart-modal-overlay").addEventListener("click", closeChartModal);
    $("close-chart-modal").addEventListener("click", closeChartModal);

    // Sizing info / formula toggle
    $("sizing-info-btn").addEventListener("click", () => {
      const box = $("formula-box");
      const isHidden = box.style.display === "none";
      box.style.display = isHidden ? "block" : "none";
      if (isHidden && state.lastSizing) updateFormula(state.lastSizing, state.safetyFactor);
    });
    $("close-formula").addEventListener("click", () => { $("formula-box").style.display = "none"; });

    // Safety slider
    const slider = $("sz-safety");
    const sliderVal = $("sz-safety-val");
    if (slider) {
      slider.addEventListener("input", () => {
        state.safetyFactor = parseFloat(slider.value);
        sliderVal.textContent = `${state.safetyFactor.toFixed(1)}×`;
      });
      slider.addEventListener("change", () => renderSizing());
    }

    initPanelCollapse();
    initResizeHandles();
    initTooltips();
    initKeyboard();

    refresh();
    state.timer = setInterval(refresh, 2000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
