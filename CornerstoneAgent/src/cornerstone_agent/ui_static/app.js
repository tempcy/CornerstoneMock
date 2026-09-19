(function () {
  "use strict";

  var pollTimer = null;
  var lastConfigJson = null;
  var lastInstruments = [];
  var lastCatalog = [];
  var draftJobs = [];
  var lastTsPayload = null;
  var selectedSampleId = null;
  var tsApplyGen = 0;
  var lastLatestBundle = null;

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setBanner(id, text, kind) {
    var el = $(id);
    if (!el) return;
    if (!text) {
      el.className = "banner hidden";
      el.textContent = "";
      return;
    }
    el.className = "banner " + (kind || "");
    el.textContent = text;
  }

  function formatUptime(sec) {
    sec = Math.max(0, parseInt(sec, 10) || 0);
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m " + s + "s";
    return s + "s";
  }

  function formatRel(iso) {
    if (!iso) return "—";
    var t = Date.parse(iso);
    if (isNaN(t)) return iso;
    var d = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (d < 5) return "刚刚";
    if (d < 60) return d + "s 前";
    if (d < 3600) return Math.floor(d / 60) + "m 前";
    if (d < 86400) return Math.floor(d / 3600) + "h 前";
    return Math.floor(d / 86400) + "d 前";
  }

  function levelClass(level) {
    if (level === "ok") return "level-ok";
    if (level === "warn") return "level-warn";
    if (level === "error") return "level-error";
    return "level-unknown";
  }

  function levelLabel(level) {
    if (level === "ok") return "● 全部可达";
    if (level === "warn") return "● 部分异常";
    if (level === "error") return "● 不可用";
    return "● —";
  }

  function rowDot(row) {
    if (row.online && row.bridge_reachable) return "ok";
    if (row.online || row.bridge_reachable) return "warn";
    return "err";
  }

  function capsLabel(caps) {
    caps = caps || [];
    if (!caps.length) return "—";
    var p0 = ["get_status", "get_sets", "get_set_reps"];
    var hasP0 = p0.every(function (c) { return caps.indexOf(c) >= 0; });
    var hasP1 = caps.indexOf("collect") >= 0;
    if (hasP0 && hasP1) return "P0+P1";
    if (hasP0) return "P0";
    return caps.length + " caps";
  }

  function bridgeVersionLabel(row) {
    var vers = (row && row.versions) || {};
    var v = vers.bridge || row.bridge_version || "";
    v = String(v || "").trim();
    return v || "—";
  }

  async function fetchJson(url, opts) {
    var res = await fetch(url, opts || {});
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = { ok: false, error: "invalid_json", status: res.status };
    }
    return { res: res, data: data };
  }

  function renderOverview(data) {
    var orch = data.orchestrator || {};
    var idn = data.identity || {};
    var sum = data.summary || {};
    var level = sum.level || "unknown";

    $("hdr-lab").textContent = idn.lab_id || "—";
    $("hdr-ver").textContent = "v" + (orch.version || "—");
    var lvl = $("hdr-level");
    lvl.className = "level-pill " + levelClass(level);
    lvl.textContent = levelLabel(level);

    $("sum-orch").textContent = data.ok ? "OK" : "FAIL";
    $("sum-online").textContent = (sum.online || 0) + " / " + (sum.instruments_total || 0);
    $("sum-bridge").textContent = (sum.bridge_ok || 0) + " / " + (sum.instruments_total || 0);
    $("sum-hb").textContent = (orch.heartbeat_interval_s || "—") + "s";
    $("sum-ttl").textContent = (orch.online_ttl_s || "—") + "s";
    $("sum-uptime").textContent = formatUptime(orch.uptime_s);

    $("ft-listen").textContent = orch.listen || "—";
    $("ft-config").textContent = orch.config_path || "—";
    $("ft-config").title = orch.config_path || "";

    var tbody = $("inst-tbody");
    tbody.innerHTML = "";
    var rows = data.instruments || [];
    lastInstruments = rows;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">暂无仪器</td></tr>';
    } else {
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        var iid = row.instrument_id || "";
        var url = row.bridge_url || "";
        var bver = bridgeVersionLabel(row);
        tr.innerHTML =
          '<td><span class="dot ' + rowDot(row) + '" title="' +
          escapeHtml((row.online ? "online" : "offline") + " / bridge " + (row.bridge_reachable ? "ok" : "down")) +
          '"></span></td>' +
          "<td><strong>" + escapeHtml(iid) + "</strong></td>" +
          '<td class="mono muted">' + escapeHtml(row.agent_id || "—") + "</td>" +
          '<td class="mono url-cell" title="' + escapeHtml(url) + '">' + escapeHtml(url || "—") + "</td>" +
          '<td class="mono" title="bridge_version">' + escapeHtml(bver) + "</td>" +
          '<td title="' + escapeHtml(row.last_seen || "") + '">' + escapeHtml(formatRel(row.last_seen)) + "</td>" +
          "<td>" + escapeHtml(capsLabel(row.capabilities)) + "</td>" +
          '<td class="col-act"><button type="button" class="btn sm" data-ping="' +
          escapeHtml(iid) + '">探测</button> <button type="button" class="btn sm" data-ts="' +
          escapeHtml(iid) + '">时序</button></td>';
        tbody.appendChild(tr);
      });
    }

    $("status-meta").textContent =
      "更新 " + (data.fetched_at || "") + " · " + rows.length + " 台";
    setBanner("status-banner", "", "");
  }

  async function refreshStatus() {
    try {
      var out = await fetchJson("/api/ui/overview");
      if (!out.data || !out.data.ok) {
        setBanner("status-banner", (out.data && out.data.error) || "overview 失败", "err");
        return;
      }
      renderOverview(out.data);
    } catch (e) {
      setBanner("status-banner", String(e.message || e), "err");
    }
  }

  async function pingOne(instrumentId) {
    var out = await fetchJson("/api/ui/instruments/ping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument_id: instrumentId }),
    });
    return out.data;
  }

  async function pingAll() {
    var btn = $("btn-ping-all");
    btn.disabled = true;
    try {
      var overview = await fetchJson("/api/ui/overview");
      var rows = (overview.data && overview.data.instruments) || [];
      var ok = 0;
      var fail = 0;
      for (var i = 0; i < rows.length; i++) {
        var r = await pingOne(rows[i].instrument_id);
        if (r && r.ok && r.bridge_reachable) ok++;
        else fail++;
      }
      setBanner(
        "status-banner",
        "探测完成：可达 " + ok + " · 失败 " + fail,
        fail ? "err" : "ok"
      );
      await refreshStatus();
    } catch (e) {
      setBanner("status-banner", String(e.message || e), "err");
    } finally {
      btn.disabled = false;
    }
  }

  function fillKv(el, pairs) {
    el.innerHTML = "";
    pairs.forEach(function (p) {
      var dt = document.createElement("dt");
      dt.textContent = p[0];
      var dd = document.createElement("dd");
      dd.textContent = p[1] == null || p[1] === "" ? "—" : String(p[1]);
      el.appendChild(dt);
      el.appendChild(dd);
    });
  }

  function readJobsFromEditor() {
    return draftJobs.map(function (j) {
      return {
        id: String(j.id || "").trim(),
        enabled: !!j.enabled,
        label: String(j.label || j.id || "").trim(),
        endpoints: (j.endpoints || []).slice(),
        interval_s: Number(j.interval_s) || 300,
        retention_days: Number(j.retention_days) || 30,
        scope: j.scope === "single" ? "single" : "all",
        instrument_ids: (j.instrument_ids || []).slice(),
      };
    }).filter(function (j) { return j.id && j.endpoints.length; });
  }

  function renderJobsEditor() {
    var host = $("jobs-editor");
    if (!host) return;
    var catalog = lastCatalog.length ? lastCatalog : [];
    var insts = (lastConfigJson && lastConfigJson.instruments) || lastInstruments || [];
    host.innerHTML = "";
    if (!draftJobs.length) {
      host.innerHTML = '<p class="muted">暂无任务。点「添加任务」或保存默认 Widgets / Ambients。</p>';
    }
    draftJobs.forEach(function (job, idx) {
      var card = document.createElement("div");
      card.className = "job-card";
      var epsHtml = catalog.map(function (c) {
        var checked = (job.endpoints || []).indexOf(c.alias) >= 0 ? " checked" : "";
        return '<label title="' + escapeHtml(c.command + " · " + (c.xml || c.rest || "")) + '">' +
          '<input type="checkbox" data-job="' + idx + '" data-ep="' + escapeHtml(c.alias) + '"' + checked + "/> " +
          escapeHtml(c.label) +
          ' <span class="job-cmd">' + escapeHtml(c.command) + "</span></label>";
      }).join("");
      var instHtml = insts.map(function (row) {
        var iid = row.instrument_id || row;
        var checked = (job.instrument_ids || []).indexOf(iid) >= 0 ? " checked" : "";
        return '<label><input type="checkbox" data-job="' + idx + '" data-iid="' + escapeHtml(iid) + '"' + checked + "/> " +
          escapeHtml(iid) + "</label>";
      }).join("");
      card.innerHTML =
        '<div class="job-head">' +
          '<label>id <input type="text" data-field="id" data-job="' + idx + '" value="' + escapeHtml(job.id || "") + '"/></label>' +
          '<label>名称 <input type="text" data-field="label" data-job="' + idx + '" value="' + escapeHtml(job.label || "") + '"/></label>' +
          '<label>周期(s) <input type="number" min="5" data-field="interval_s" data-job="' + idx + '" value="' + escapeHtml(job.interval_s || 300) + '"/></label>' +
          '<label>有效期(天) <input type="number" min="1" data-field="retention_days" data-job="' + idx + '" value="' + escapeHtml(job.retention_days || 30) + '"/></label>' +
          '<label class="check"><input type="checkbox" data-field="enabled" data-job="' + idx + '"' + (job.enabled !== false ? " checked" : "") + "/> 启用</label>" +
          '<button type="button" class="btn" data-del="' + idx + '">删除</button>' +
        "</div>" +
        "<div><strong>查询命令</strong></div>" +
        '<div class="job-eps">' + (epsHtml || '<span class="muted">无清单</span>') + "</div>" +
        '<div class="job-scope">' +
          '<label><input type="radio" name="scope-' + idx + '" data-field="scope" data-job="' + idx + '" value="all"' + (job.scope !== "single" ? " checked" : "") + "/> 全部设备</label>" +
          '<label><input type="radio" name="scope-' + idx + '" data-field="scope" data-job="' + idx + '" value="single"' + (job.scope === "single" ? " checked" : "") + "/> 指定仪器</label>" +
        "</div>" +
        '<div class="job-inst">' + (instHtml || '<span class="muted">配置中无仪器</span>') + "</div>";
      host.appendChild(card);
    });
  }

  function renderConfig(data) {
    lastConfigJson = data;
    var idn = data.identity || {};
    var orch = data.orchestrator || {};
    var priv = data.privacy || {};

    fillKv($("cfg-identity"), [
      ["org_id", idn.org_id],
      ["lab_id", idn.lab_id],
      ["instrument_id", idn.instrument_id],
      ["agent_id", idn.agent_id],
    ]);
    fillKv($("cfg-orch"), [
      ["listen", (orch.listen_host || "") + ":" + (orch.listen_port || "")],
      ["heartbeat_interval_s", orch.heartbeat_interval_s],
      ["online_ttl_s", orch.online_ttl_s],
      ["embed_local_agent", orch.embed_local_agent],
      ["registry_path", orch.registry_path],
      ["snapshot_dir", orch.snapshot_dir],
    ]);
    fillKv($("cfg-privacy"), [
      ["redact_sample_names", priv.redact_sample_names],
    ]);
    var ts = data.timeseries || {};
    if ($("cfg-ts")) {
      var jobs = ts.jobs || [];
      fillKv($("cfg-ts"), [
        ["enabled", ts.enabled],
        ["timeout_s", ts.timeout_s],
        ["db_path", ts.db_path],
        ["jobs", jobs.length],
      ]);
    }
    lastCatalog = data.catalog || lastCatalog || [];
    draftJobs = (ts.jobs || []).map(function (j) { return Object.assign({}, j, {
      endpoints: (j.endpoints || []).slice(),
      instrument_ids: (j.instrument_ids || []).slice(),
    }); });
    renderJobsEditor();

    var tbody = $("cfg-inst-tbody");
    tbody.innerHTML = "";
    (data.instruments || []).forEach(function (row) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td><strong>" + escapeHtml(row.instrument_id) + "</strong></td>" +
        '<td class="mono muted">' + escapeHtml(row.agent_id || "") + "</td>" +
        "<td>" + escapeHtml(row.lab_id || "") + "</td>" +
        '<td class="mono">' + escapeHtml(row.bridge_url || "") + "</td>";
      tbody.appendChild(tr);
    });

    $("config-meta").textContent =
      (data.read_only ? "只读 · " : "采集任务可保存 · ") + (data.config_path || "");
    $("cfg-json").textContent = JSON.stringify(data, null, 2);
    setBanner("config-banner", "", "");
  }

  async function refreshConfig() {
    try {
      var out = await fetchJson("/api/ui/config");
      if (!out.data || !out.data.ok) {
        setBanner("config-banner", (out.data && out.data.error) || "config 失败", "err");
        return;
      }
      renderConfig(out.data);
    } catch (e) {
      setBanner("config-banner", String(e.message || e), "err");
    }
  }

  var PREFERRED_METRICS = [
    "gauge.FurnaceTemperature",
    "gauge.DustFilterTemperature",
    "gauge.CatalystTemperature",
    "gauge.BackPressure",
  ];

  function isGaugeMetric(m) {
    m = String(m || "");
    if (m.indexOf("gauge.") === 0 && m.indexOf(".warning") < 0) return true;
    if (m.indexOf("ambient.") === 0 && m.indexOf(".warning") < 0) return true;
    if (m.indexOf("param.") === 0) {
      var x = m.toLowerCase();
      return /temp|pressure|flow|furnace|catalyst|dust|heater|背压|流量|压力|温度/.test(x);
    }
    return false;
  }

  function gaugesOnly() {
    var el = $("ts-gauges-only");
    return !el || el.checked;
  }

  function filterMetrics(metrics) {
    metrics = metrics || [];
    var job = $("ts-job") ? $("ts-job").value : "";
    if (job === "widgets") {
      var w = metrics.filter(function (m) { return String(m).indexOf("gauge.") === 0; });
      return w.length ? w : metrics.slice();
    }
    if (job === "ambients") {
      var a = metrics.filter(function (m) { return String(m).indexOf("ambient.") === 0; });
      return a.length ? a : metrics.slice();
    }
    if (!gaugesOnly()) return metrics.slice();
    var g = metrics.filter(isGaugeMetric);
    return g.length ? g : metrics.slice();
  }

  function metricLabel(m) {
    if (m.indexOf("gauge.") === 0) return m.slice("gauge.".length);
    if (m.indexOf("ambient.") === 0) return m.slice("ambient.".length);
    return m;
  }

  function ensureInstrumentOption(sel, iid) {
    if (!sel || !iid) return;
    var found = false;
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === iid) found = true;
    }
    if (!found) {
      var opt = document.createElement("option");
      opt.value = iid;
      opt.textContent = iid;
      sel.appendChild(opt);
    }
  }

  function fillInstrumentSelect() {
    var sel = $("ts-instrument");
    if (!sel) return;
    var prev = sel.value;
    sel.innerHTML = "";
    (lastInstruments || []).forEach(function (row) {
      var opt = document.createElement("option");
      opt.value = row.instrument_id;
      opt.textContent = row.instrument_id;
      sel.appendChild(opt);
    });
    if (prev) {
      ensureInstrumentOption(sel, prev);
      sel.value = prev;
    } else if (sel.options.length) {
      sel.selectedIndex = 0;
    }
  }

  function fillJobSelect(jobs) {
    var sel = $("ts-job");
    if (!sel) return;
    var prev = sel.value;
    sel.innerHTML = '<option value="">全部</option>';
    (jobs || []).forEach(function (j) {
      var opt = document.createElement("option");
      opt.value = j.id;
      opt.textContent = (j.label || j.id) + " · " + (j.interval_s || "?") + "s / " + (j.retention_days || "?") + "d";
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
    fillSampleJobSelect(jobs);
  }

  function fillSampleJobSelect(jobs) {
    var sel = $("ts-sample-job");
    if (!sel) return;
    var prev = sel.value;
    sel.innerHTML = '<option value="">全部</option>';
    (jobs || []).forEach(function (j) {
      var opt = document.createElement("option");
      opt.value = j.id;
      opt.textContent = j.label || j.id;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  }

  function jobLabel(id) {
    if (!id) return "";
    var jobs = (lastTsPayload && lastTsPayload.jobs) || [];
    for (var i = 0; i < jobs.length; i++) {
      if (jobs[i].id === id) return jobs[i].label || id;
    }
    return id;
  }

  function sampleJobFilter() {
    var el = $("ts-sample-job");
    return (el && el.value) || "";
  }

  function filteredSamples() {
    var all = (lastTsPayload && lastTsPayload.samples) || [];
    var job = sampleJobFilter();
    if (!job) return all.slice();
    return all.filter(function (r) { return String(r.job_id || "") === job; });
  }

  function tsQueryExtra() {
    var extra = "";
    var job = $("ts-job") && $("ts-job").value;
    if (job) extra += "&job_id=" + encodeURIComponent(job);
    return extra;
  }

  function pickMetric(metrics, current) {
    var pool = filterMetrics(metrics);
    if (current && pool.indexOf(current) >= 0) return current;
    for (var i = 0; i < PREFERRED_METRICS.length; i++) {
      if (pool.indexOf(PREFERRED_METRICS[i]) >= 0) return PREFERRED_METRICS[i];
    }
    var keys = ["furnace", "dust", "catalyst", "backpressure", "pressure", "flow", "temp"];
    for (var k = 0; k < keys.length; k++) {
      for (var j = 0; j < pool.length; j++) {
        if (pool[j].toLowerCase().indexOf(keys[k]) >= 0) return pool[j];
      }
    }
    return pool[0] || "";
  }

  function sampleRowLimit() {
    var el = $("ts-sample-limit");
    var n = parseInt(el && el.value, 10);
    if (n === 20 || n === 50 || n === 100) return n;
    return 10;
  }

  function syncTsScrollHeight() {
    var vis = Math.min(sampleRowLimit(), 10);
    document.documentElement.style.setProperty("--ts-vis-rows", String(vis));
  }

  function renderTsSamples() {
    var stb = $("ts-samples-tbody");
    if (!stb) return;
    var limit = sampleRowLimit();
    var rows = filteredSamples().slice(0, limit);
    stb.innerHTML = "";
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-sid", String(row.id || ""));
      if (selectedSampleId && String(row.id) === String(selectedSampleId)) {
        tr.className = "selected";
      }
      tr.innerHTML =
        "<td title=\"" + escapeHtml(row.ts || "") + "\">" + escapeHtml(formatRel(row.ts)) + "</td>" +
        "<td class=\"muted\">" + escapeHtml(jobLabel(row.job_id)) + "</td>" +
        "<td>" + (row.ok ? "OK" : "FAIL") + "</td>" +
        "<td>" + escapeHtml(row.point_count) + "</td>" +
        "<td>" + escapeHtml(row.duration_ms) + "ms</td>";
      stb.appendChild(tr);
    });
    if (!rows.length) {
      stb.innerHTML = '<tr><td colspan="5" class="muted">暂无样本</td></tr>';
    }
    syncTsScrollHeight();
  }

  function renderLatestPoints(bundle) {
    var ltb = $("ts-latest-tbody");
    var cap = $("ts-latest-caption");
    if (bundle) lastLatestBundle = bundle;
    if (!ltb) return;
    var sample = bundle && bundle.sample;
    var vals = (bundle && bundle.values) || [];
    if (gaugesOnly()) {
      var gvals = vals.filter(function (row) { return isGaugeMetric(row.metric); });
      if (gvals.length) vals = gvals;
    }
    if (cap) {
      if (selectedSampleId && sample) {
        cap.textContent = "选中 · " + formatRel(sample.ts) +
          (sample.job_id ? " · " + jobLabel(sample.job_id) : "");
      } else {
        cap.textContent = "最新";
      }
    }
    ltb.innerHTML = "";
    vals.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="mono">' + escapeHtml(metricLabel(row.metric)) + "</td>" +
        "<td>" + escapeHtml(row.value) + "</td>" +
        "<td class=\"muted\">" + escapeHtml(row.unit || "") + "</td>";
      ltb.appendChild(tr);
    });
    if (!vals.length) {
      ltb.innerHTML = '<tr><td colspan="3" class="muted">暂无点</td></tr>';
    }
  }

  async function selectSample(id) {
    var gen = ++tsApplyGen;
    selectedSampleId = id;
    renderTsSamples();
    try {
      var out = await fetchJson("/api/ui/timeseries/sample?sample_id=" + encodeURIComponent(id));
      if (gen !== tsApplyGen) return;
      if (!out.data || !out.data.ok) {
        setBanner("ts-banner", (out.data && (out.data.error || out.data.message)) || "读取样本失败", "err");
        return;
      }
      renderLatestPoints(out.data);
    } catch (e) {
      if (gen !== tsApplyGen) return;
      setBanner("ts-banner", String(e.message || e), "err");
    }
  }

  function applySampleJobFilter() {
    if (selectedSampleId) {
      var still = filteredSamples().some(function (r) {
        return String(r.id) === String(selectedSampleId);
      });
      if (!still) {
        selectedSampleId = null;
        renderLatestPoints(lastTsPayload && lastTsPayload.latest);
      }
    }
    renderTsSamples();
  }

  function drawSeries(canvas, series) {
    var empty = $("ts-chart-empty");
    var ctx = canvas.getContext("2d");
    var w = canvas.width;
    var h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!series || !series.length) {
      if (empty) empty.classList.remove("hidden");
      return;
    }
    if (empty) empty.classList.add("hidden");
    var nums = series.map(function (p) {
      var v = p.value;
      if (typeof v === "boolean") return v ? 1 : 0;
      var n = Number(v);
      return isNaN(n) ? null : n;
    }).filter(function (v) { return v !== null; });
    if (!nums.length) {
      if (empty) empty.classList.remove("hidden");
      return;
    }
    var min = Math.min.apply(null, nums);
    var max = Math.max.apply(null, nums);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    var pad = 16;
    ctx.strokeStyle = "#333";
    ctx.beginPath();
    ctx.moveTo(pad, pad);
    ctx.lineTo(pad, h - pad);
    ctx.lineTo(w - pad, h - pad);
    ctx.stroke();
    ctx.strokeStyle = "#c9a227";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    var usable = series.length > 1 ? series.length - 1 : 1;
    series.forEach(function (p, i) {
      var v = p.value;
      if (typeof v === "boolean") v = v ? 1 : 0;
      v = Number(v);
      if (isNaN(v)) return;
      var x = pad + (i / usable) * (w - pad * 2);
      var y = h - pad - ((v - min) / (max - min)) * (h - pad * 2);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = "#9aa0a6";
    ctx.font = "11px sans-serif";
    ctx.fillText(String(max), 2, pad + 4);
    ctx.fillText(String(min), 2, h - 4);
  }

  async function refreshTimeseries(opts) {
    opts = opts || {};
    if (opts.resetSelection) {
      selectedSampleId = null;
      tsApplyGen++;
    }
    var startGen = tsApplyGen;
    if (!$("view-timeseries") || $("view-timeseries").classList.contains("hidden")) return;
    if (!lastInstruments.length) {
      try {
        var ov = await fetchJson("/api/ui/overview");
        if (ov.data && ov.data.instruments) lastInstruments = ov.data.instruments;
      } catch (e) {}
    }
    fillInstrumentSelect();
    var iid = $("ts-instrument").value;
    var hours = $("ts-hours").value || "24";
    var metricWanted = $("ts-metric").value;
    if (!iid) {
      setBanner("ts-banner", "没有可选仪器", "err");
      return;
    }
    try {
      var url = "/api/ui/timeseries?instrument_id=" + encodeURIComponent(iid) + "&hours=" + encodeURIComponent(hours) + tsQueryExtra();
      if (metricWanted) url += "&metric=" + encodeURIComponent(metricWanted);
      var out = await fetchJson(url);
      if (!out.data || !out.data.ok) {
        setBanner("ts-banner", (out.data && (out.data.error || out.data.message)) || "timeseries 失败", "err");
        return;
      }
      var data = out.data;
      fillJobSelect(data.jobs || []);
      var metrics = data.metrics || [];
      var shown = filterMetrics(metrics);
      var msel = $("ts-metric");
      var chosen = pickMetric(metrics, metricWanted);
      msel.innerHTML = "";
      shown.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m;
        opt.textContent = metricLabel(m);
        msel.appendChild(opt);
      });
      if (chosen) msel.value = chosen;
      if (chosen && chosen !== data.metric) {
        var out2 = await fetchJson(
          "/api/ui/timeseries?instrument_id=" + encodeURIComponent(iid) +
          "&hours=" + encodeURIComponent(hours) + "&metric=" + encodeURIComponent(chosen) + tsQueryExtra()
        );
        if (out2.data && out2.data.ok) data = out2.data;
      }
      var st = data.stats || {};
      $("ts-sum-n").textContent = st.samples == null ? "—" : st.samples;
      $("ts-sum-ok").textContent = st.ok == null ? "—" : st.ok;
      $("ts-sum-fail").textContent = st.fail == null ? "—" : st.fail;
      $("ts-sum-iv").textContent = (data.interval_s || "—") + "s";
      var last = data.latest && data.latest.sample;
      $("ts-sum-last").textContent = last ? formatRel(last.ts) : "尚无";
      drawSeries($("ts-chart"), data.series || []);
      lastTsPayload = data;
      if (tsApplyGen !== startGen) return;
      renderTsSamples();
      var keep = selectedSampleId && filteredSamples().some(function (r) {
        return String(r.id) === String(selectedSampleId);
      });
      if (!keep) {
        selectedSampleId = null;
        renderLatestPoints(data.latest);
      }

      $("ts-meta").textContent =
        iid + " · " + hours + "h · " + (chosen || "无指标") +
        (last && last.error ? " · last error: " + last.error : "");
      setBanner("ts-banner", "", "");
    } catch (e) {
      setBanner("ts-banner", String(e.message || e), "err");
    }
  }

  function showView(name) {
    $("view-status").classList.toggle("hidden", name !== "status");
    $("view-config").classList.toggle("hidden", name !== "config");
    var tsView = $("view-timeseries");
    if (tsView) tsView.classList.toggle("hidden", name !== "timeseries");
    document.querySelectorAll(".nav-top").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-view") === name);
    });
    if (name === "config") refreshConfig();
    else if (name === "timeseries") refreshTimeseries();
    else refreshStatus();
  }

  function schedulePoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if ($("auto-refresh").checked) {
      pollTimer = setInterval(function () {
        if (!$("view-status").classList.contains("hidden")) refreshStatus();
        else if ($("view-timeseries") && !$("view-timeseries").classList.contains("hidden")) refreshTimeseries();
      }, 10000);
    }
  }

  document.querySelectorAll(".nav-top").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showView(btn.getAttribute("data-view"));
    });
  });

  $("btn-refresh").addEventListener("click", refreshStatus);
  $("btn-ping-all").addEventListener("click", pingAll);
  $("auto-refresh").addEventListener("change", schedulePoll);
  $("btn-cfg-refresh").addEventListener("click", refreshConfig);
  $("btn-cfg-copy").addEventListener("click", function () {
    if (!lastConfigJson) return;
    var text = JSON.stringify(lastConfigJson, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { setBanner("config-banner", "已复制到剪贴板", "ok"); },
        function () {
          $("cfg-json").hidden = false;
          setBanner("config-banner", "无法写剪贴板，已展开 JSON", "err");
        }
      );
    } else {
      $("cfg-json").hidden = false;
      setBanner("config-banner", "已展开 JSON，请手动复制", "ok");
    }
  });

  $("inst-tbody").addEventListener("click", function (ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) return;
    var id = t.getAttribute("data-ping");
    if (id) {
      t.disabled = true;
      pingOne(id).then(function (r) {
        if (r && r.ok) {
          var verBit = r.bridge_version ? " · v" + r.bridge_version : "";
          setBanner(
            "status-banner",
            id + " · " + (r.bridge_reachable ? "Bridge 可达" : "Bridge 不可达") +
              verBit + " · " + (r.bridge_url || ""),
            r.bridge_reachable ? "ok" : "err"
          );
        } else {
          setBanner("status-banner", (r && (r.message || r.error)) || "探测失败", "err");
        }
        return refreshStatus();
      }).finally(function () { t.disabled = false; });
      return;
    }
    var tsId = t.getAttribute("data-ts");
    if (tsId) {
      var sel = $("ts-instrument");
      if (sel) {
        ensureInstrumentOption(sel, tsId);
        sel.value = tsId;
      }
      showView("timeseries");
    }
  });

  if ($("btn-ts-refresh")) {
    $("btn-ts-refresh").addEventListener("click", function () {
      refreshTimeseries({ resetSelection: true });
    });
    function refreshTsReset() {
      refreshTimeseries({ resetSelection: true });
    }
    $("ts-instrument").addEventListener("change", refreshTsReset);
    $("ts-hours").addEventListener("change", refreshTsReset);
    $("ts-metric").addEventListener("change", refreshTsReset);
    if ($("ts-job")) $("ts-job").addEventListener("change", refreshTsReset);
    if ($("ts-sample-limit")) {
      $("ts-sample-limit").addEventListener("change", function () {
        renderTsSamples();
      });
    }
    if ($("ts-sample-job")) {
      $("ts-sample-job").addEventListener("change", applySampleJobFilter);
    }
    if ($("ts-samples-tbody")) {
      $("ts-samples-tbody").addEventListener("click", function (ev) {
        var t = ev.target;
        while (t && t !== this && !(t.getAttribute && t.getAttribute("data-sid"))) {
          t = t.parentNode;
        }
        if (!t || !t.getAttribute) return;
        var sid = t.getAttribute("data-sid");
        if (sid) selectSample(sid);
      });
    }
    if ($("ts-gauges-only")) {
      $("ts-gauges-only").addEventListener("change", function () {
        refreshTimeseries();
        if (lastLatestBundle) renderLatestPoints(lastLatestBundle);
      });
    }
    $("btn-ts-collect").addEventListener("click", async function () {
      var btn = $("btn-ts-collect");
      var iid = $("ts-instrument").value;
      if (!iid) return;
      btn.disabled = true;
      try {
        var payload = { instrument_id: iid };
        var job = $("ts-job") && $("ts-job").value;
        if (job) payload.job_id = job;
        var out = await fetchJson("/api/ui/timeseries/collect-once", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        var d = out.data || {};
        setBanner(
          "ts-banner",
          d.ok
            ? iid + " 已采集 · " + (d.point_count || 0) + " 点 · " + (d.duration_ms || 0) + "ms"
            : (d.error || "采集失败"),
          d.ok ? "ok" : "err"
        );
        await refreshTimeseries();
      } catch (e) {
        setBanner("ts-banner", String(e.message || e), "err");
      } finally {
        btn.disabled = false;
      }
    });
    $("btn-ts-export").addEventListener("click", function () {
      var iid = $("ts-instrument").value;
      var hours = $("ts-hours").value || "24";
      var metric = $("ts-metric").value;
      if (!iid) return;
      var url = "/api/ui/timeseries/export.csv?instrument_id=" + encodeURIComponent(iid) +
        "&hours=" + encodeURIComponent(hours) + tsQueryExtra();
      if (metric) url += "&metric=" + encodeURIComponent(metric);
      window.location.href = url;
    });
  }

  if ($("jobs-editor")) {
    $("jobs-editor").addEventListener("input", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var idx = parseInt(t.getAttribute("data-job"), 10);
      if (isNaN(idx) || !draftJobs[idx]) return;
      var field = t.getAttribute("data-field");
      if (field === "id" || field === "label") draftJobs[idx][field] = t.value;
      if (field === "interval_s" || field === "retention_days") draftJobs[idx][field] = Number(t.value);
    });
    $("jobs-editor").addEventListener("change", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var idx = parseInt(t.getAttribute("data-job"), 10);
      if (isNaN(idx) || !draftJobs[idx]) return;
      var field = t.getAttribute("data-field");
      if (field === "enabled") draftJobs[idx].enabled = !!t.checked;
      if (field === "scope") draftJobs[idx].scope = t.value;
      if (t.getAttribute("data-ep")) {
        var alias = t.getAttribute("data-ep");
        var eps = draftJobs[idx].endpoints || [];
        if (t.checked && eps.indexOf(alias) < 0) eps.push(alias);
        if (!t.checked) eps = eps.filter(function (x) { return x !== alias; });
        draftJobs[idx].endpoints = eps;
      }
      if (t.getAttribute("data-iid")) {
        var iid = t.getAttribute("data-iid");
        var ids = draftJobs[idx].instrument_ids || [];
        if (t.checked && ids.indexOf(iid) < 0) ids.push(iid);
        if (!t.checked) ids = ids.filter(function (x) { return x !== iid; });
        draftJobs[idx].instrument_ids = ids;
      }
    });
    $("jobs-editor").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var del = t.getAttribute("data-del");
      if (del == null) return;
      var idx = parseInt(del, 10);
      if (isNaN(idx)) return;
      draftJobs.splice(idx, 1);
      renderJobsEditor();
    });
  }
  if ($("btn-job-add")) {
    $("btn-job-add").addEventListener("click", function () {
      draftJobs.push({
        id: "job-" + (draftJobs.length + 1),
        enabled: true,
        label: "自定义",
        endpoints: ["status-widgets"],
        interval_s: 60,
        retention_days: 7,
        scope: "all",
        instrument_ids: [],
      });
      renderJobsEditor();
    });
  }
  if ($("btn-jobs-save")) {
    $("btn-jobs-save").addEventListener("click", async function () {
      var btn = $("btn-jobs-save");
      btn.disabled = true;
      try {
        var ts = (lastConfigJson && lastConfigJson.timeseries) || {};
        var out = await fetchJson("/api/ui/timeseries/jobs", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: ts.enabled !== false,
            timeout_s: ts.timeout_s || 90,
            jobs: readJobsFromEditor(),
          }),
        });
        var d = out.data || {};
        if (!d.ok) {
          setBanner("jobs-banner", d.error || d.message || "保存失败", "err");
          return;
        }
        setBanner("jobs-banner", "已保存并热加载 " + ((d.timeseries && d.timeseries.jobs) || []).length + " 个任务", "ok");
        await refreshConfig();
      } catch (e) {
        setBanner("jobs-banner", String(e.message || e), "err");
      } finally {
        btn.disabled = false;
      }
    });
  }

  showView("status");
  schedulePoll();
})();
