(function () {
  "use strict";

  var pollTimer = null;
  var lastConfigJson = null;

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
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">暂无仪器</td></tr>';
    } else {
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        var iid = row.instrument_id || "";
        var url = row.bridge_url || "";
        tr.innerHTML =
          '<td><span class="dot ' + rowDot(row) + '" title="' +
          escapeHtml((row.online ? "online" : "offline") + " / bridge " + (row.bridge_reachable ? "ok" : "down")) +
          '"></span></td>' +
          "<td><strong>" + escapeHtml(iid) + "</strong></td>" +
          '<td class="mono muted">' + escapeHtml(row.agent_id || "—") + "</td>" +
          '<td class="mono url-cell" title="' + escapeHtml(url) + '">' + escapeHtml(url || "—") + "</td>" +
          '<td title="' + escapeHtml(row.last_seen || "") + '">' + escapeHtml(formatRel(row.last_seen)) + "</td>" +
          "<td>" + escapeHtml(capsLabel(row.capabilities)) + "</td>" +
          '<td class="col-act"><button type="button" class="btn sm" data-ping="' +
          escapeHtml(iid) + '">探测</button></td>';
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

  function renderConfig(data) {
    lastConfigJson = data;
    var idn = data.identity || {};
    var br = data.bridge || {};
    var orch = data.orchestrator || {};
    var priv = data.privacy || {};

    fillKv($("cfg-identity"), [
      ["org_id", idn.org_id],
      ["lab_id", idn.lab_id],
      ["instrument_id", idn.instrument_id],
      ["agent_id", idn.agent_id],
    ]);
    fillKv($("cfg-bridge"), [
      ["base_url", br.base_url],
      ["timeout_s", br.timeout_s],
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
      (data.read_only ? "只读 · " : "") + (data.config_path || "");
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

  function showView(name) {
    $("view-status").classList.toggle("hidden", name !== "status");
    $("view-config").classList.toggle("hidden", name !== "config");
    document.querySelectorAll(".nav-top").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-view") === name);
    });
    if (name === "config") refreshConfig();
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
    if (!id) return;
    t.disabled = true;
    pingOne(id).then(function (r) {
      if (r && r.ok) {
        setBanner(
          "status-banner",
          id + " · " + (r.bridge_reachable ? "Bridge 可达" : "Bridge 不可达") + " · " + (r.bridge_url || ""),
          r.bridge_reachable ? "ok" : "err"
        );
      } else {
        setBanner("status-banner", (r && (r.message || r.error)) || "探测失败", "err");
      }
      return refreshStatus();
    }).finally(function () { t.disabled = false; });
  });

  showView("status");
  schedulePoll();
})();
