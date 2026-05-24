(function () {
  "use strict";

  var pollTimer = null;
  var statusPollTimer = null;
  var lastHeartbeatReplyAt = -1;
  var statusPollSeen = false;
  var lastGwSettings = null;

  function $(id) { return document.getElementById(id); }

  function show(el, on) {
    if (!el) return;
    el.classList.toggle("hidden", !on);
  }

  function setBanner(id, text, kind) {
    var el = $(id);
    if (!el) return;
    el.textContent = text || "";
    el.className = "banner" + (text ? " " + (kind || "warn") : " hidden");
  }

  async function fetchJson(url, opts) {
    var r = await fetch(url, opts || {});
    var data = null;
    try { data = await r.json(); } catch (e) { data = { ok: false, error: "非 JSON 应答" }; }
    if (!r.ok && data && !data.error) data.error = "HTTP " + r.status;
    return data;
  }

  function applyQueueFooterFromPayload(data) {
    if (!data) return;
    var qm = $("footer-queue-max");
    if (qm && data.queueMax != null) qm.textContent = String(data.queueMax);
    var btn = $("btn-footer-right");
    if (btn) {
      var cur = data.queueCount != null ? data.queueCount : 0;
      var maxs =
        data.queueMax != null ? String(data.queueMax) : (qm && qm.textContent !== "—" ? qm.textContent : "—");
      btn.title = "当前截留 " + cur + " 条 · 队列上限 " + maxs + " · 点击修改配置";
    }
  }

  function updateFooterQueueCurrentOnly(n) {
    var btn = $("btn-footer-right");
    var qm = $("footer-queue-max");
    if (!btn) return;
    var maxs = qm && qm.textContent ? qm.textContent : "—";
    btn.title = "当前截留 " + n + " 条 · 队列上限 " + maxs + " · 点击修改配置";
  }

  function applyRcsFromPayload(data) {
    if (!data) return;
    var dot = $("rcs-state-dot");
    var box = $("rcs-box");
    var disp =
      data.remoteControlState != null && data.remoteControlState !== ""
        ? String(data.remoteControlState)
        : "—";
    var tip = "RemoteControlState: " + disp;
    if (data.privilegedAddSamplesHost) {
      tip += " · AddSamples 直通（配置）: " + data.privilegedAddSamplesHost;
    }
    if (data.remoteControlStateError) {
      tip += " · " + data.remoteControlStateError;
    }
    if (box) box.title = tip;
    if (dot) {
      dot.classList.remove("rcs-state-true", "rcs-state-false", "rcs-state-unknown");
      var raw = (data.remoteControlState && String(data.remoteControlState).trim().toLowerCase()) || "";
      if (raw === "true" || raw === "1" || raw === "yes") {
        dot.classList.add("rcs-state-true");
      } else if (raw === "false" || raw === "0" || raw === "no") {
        dot.classList.add("rcs-state-false");
      } else {
        dot.classList.add("rcs-state-unknown");
      }
      dot.textContent = "●";
      dot.title = tip;
      dot.setAttribute("aria-label", "RemoteControlState " + disp);
    }
  }

  function applyStatusPayload(data) {
    if (!data || !data.ok) return;
    var heart = $("conn-icon-heart");
    var bolt = $("conn-icon-bolt");
    var hb = Number(data.lastHeartbeatReplyAt) || 0;
    if (data.upstreamConnected) {
      show(heart, true);
      show(bolt, false);
      if (statusPollSeen && hb > lastHeartbeatReplyAt + 1e-9) {
        heart.classList.remove("flash");
        void heart.offsetWidth;
        heart.classList.add("flash");
        setTimeout(function () {
          if (heart) heart.classList.remove("flash");
        }, 420);
      }
    } else {
      show(heart, false);
      show(bolt, true);
    }
    lastHeartbeatReplyAt = hb;
    statusPollSeen = true;
    applyQueueFooterFromPayload(data);
    applyRcsFromPayload(data);
  }

  async function pollGatewayStatus() {
    var data = await fetchJson("/api/status");
    applyStatusPayload(data);
  }

  function startStatusPolling() {
    if (statusPollTimer) clearInterval(statusPollTimer);
    statusPollTimer = setInterval(pollGatewayStatus, 1000);
    void pollGatewayStatus();
  }

  async function refreshInstrumentVersionSummary() {
    var ver = $("instrument-version");
    if (!ver) return;
    var data = await fetchJson("/api/instrument/instrument-info");
    if (data && data.ok) ver.textContent = data.versionSummary || "—";
    else ver.textContent = "—";
  }

  async function openInstrumentInfoModal() {
    var backdrop = $("modal-instrument-info");
    var body = $("modal-ii-body");
    if (!backdrop || !body) return;
    show(backdrop, true);
    body.textContent = "加载中…";
    var data = await fetchJson("/api/instrument/instrument-info");
    var ver = $("instrument-version");
    if (data && data.ok) {
      body.innerHTML = buildInstrumentInfoTablesHtml(data.xml || "");
      if (ver) ver.textContent = data.versionSummary || "—";
    } else {
      body.textContent = (data && data.error) ? data.error : "加载失败";
    }
  }

  function closeInstrumentInfoModal() {
    show($("modal-instrument-info"), false);
  }

  function fillGatewaySettingsForm(s) {
    if (!s) return;
    var th = $("sett-tcp-host");
    var tp = $("sett-tcp-port");
    var wh = $("sett-web-host");
    var wp = $("sett-web-port");
    var uh = $("sett-up-host");
    var up = $("sett-up-port");
    var wu = $("sett-web-user");
    var wpp = $("sett-web-password");
    var ph = $("sett-priv-host");
    var qm = $("sett-queue-max");
    var qcur = $("sett-queue-current");
    if (th) th.value = s.tcpListenHost != null ? String(s.tcpListenHost) : "";
    if (tp) tp.value = s.tcpListenPort != null ? String(s.tcpListenPort) : "";
    if (wh) wh.value = s.webListenHost != null ? String(s.webListenHost) : "";
    if (wp) wp.value = s.webListenPort != null ? String(s.webListenPort) : "";
    if (uh) uh.value = s.upstreamHost != null ? String(s.upstreamHost) : "";
    if (up) up.value = s.upstreamPort != null ? String(s.upstreamPort) : "";
    if (wu) wu.value = s.webUser != null ? String(s.webUser) : "";
    if (wpp) wpp.value = "";
    if (wpp) {
      wpp.placeholder = s.webPasswordSet
        ? "新密码（留空不修改）"
        : "密码（留空则保持未设置）";
    }
    if (ph) ph.value = s.privilegedAddSamplesHost != null ? String(s.privilegedAddSamplesHost) : "";
    if (qm) qm.value = s.queueMax != null ? String(s.queueMax) : "";
    if (qcur) {
      qcur.textContent =
        s.queueCurrent != null ? "当前截留 " + s.queueCurrent + " 条" : "";
    }
    var cp = $("modal-gw-configpath");
    if (cp) {
      cp.textContent = s.configFile
        ? "配置文件: " + s.configFile
        : "未使用 --config 启动，无法写回 JSON（仍可在内存中修改，重启后丢失）。";
    }
  }

  async function openGatewaySettingsModal() {
    var backdrop = $("modal-gateway-settings");
    if (!backdrop) return;
    var res = $("modal-gw-result");
    if (res) {
      res.textContent = "";
      show(res, false);
    }
    show(backdrop, true);
    var data = await fetchJson("/api/settings");
    if (data && data.ok) {
      lastGwSettings = data;
      fillGatewaySettingsForm(data);
    }
  }

  function closeGatewaySettingsModal() {
    show($("modal-gateway-settings"), false);
  }

  var transportsDetailCache = Object.create(null);
  var transportsExpandedKey = null;

  var methodsDetailCache = Object.create(null);
  var methodsExpandedKey = null;

  var standardsDetailCache = Object.create(null);
  var standardsExpandedKey = null;

  function resetTransportsDetailUi() {
    transportsExpandedKey = null;
    var body = $("tp-body");
    if (body) body.classList.remove("tp-body--split");
    var det = $("tp-detail");
    if (det) det.classList.add("hidden");
    var inner = $("tp-detail-inner");
    if (inner) inner.innerHTML = "";
    document.querySelectorAll("#tp-list .tp-row.tp-row--active").forEach(function (r) {
      r.classList.remove("tp-row--active");
    });
    document.querySelectorAll("#tp-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
  }

  function tpScalarTitleZh(tag, fallbackLabel) {
    var m = {
      Name: "名称",
      Description: "说明",
      LastUsed: "上次使用",
      LastModified: "上次修改时间",
      Excluded: "已排除",
      TransmitOption: "传输选项",
      TransportAutomatically: "自动传送",
      TransportFormat: "传送格式",
      TransportUnits: "传送附有单位的结果",
      TransportRangeAnnotation: "传送范围标注",
      CharacterEncoding: "字符译码",
      ExportOptions: "导出选项",
      ExportFileName: "导出文件名",
      TransmitBegin: "传送开始",
      TransmitEnd: "传送结束",
      SetEnd: "Set 结束",
      ReplicateEnd: "重复结束",
      FieldBegin: "字段开始",
      FieldEnd: "字段结束"
    };
    return (tag && m[tag]) || (fallbackLabel && String(fallbackLabel).trim()) || tag || "—";
  }

  function tpDisplayScalarValue(tag, raw) {
    var v = raw == null ? "" : String(raw).trim();
    var tl = (tag || "").trim();
    if (tl === "TransmitOption") {
      if (v === "Ethernet") return "以太网";
    }
    if (tl === "TransportFormat") {
      if (v === "XmlFormat") return "XML 格式";
      if (v === "TextFormat") return "文本格式";
    }
    if (
      tl === "TransportAutomatically" ||
      tl === "TransportUnits" ||
      tl === "TransportRangeAnnotation"
    ) {
      var lo = v.toLowerCase();
      if (lo === "true") return "是";
      if (lo === "false") return "否";
    }
    if (tl === "ExportOptions") {
      if (v === "Overwrite") return "覆盖";
    }
    if (tl === "ExportFileName" && v === "[Default]") return "[默认]";
    if (!v) return "—";
    return v;
  }

  function renderTransportsList(items) {
    var box = $("tp-list");
    if (!box) return;
    box.innerHTML = "";
    if (!items || !items.length) {
      box.innerHTML = '<p class="muted tp-list-empty">暂无传送项</p>';
      return;
    }
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "tp-row";
      row.dataset.key = it.key || "";
      var ex = !!it.excluded;
      var lu = (it.lastUsed && String(it.lastUsed).trim()) || "—";
      var lm = (it.lastModified && String(it.lastModified).trim()) || "—";
      var desc = (it.description && String(it.description).trim()) || "—";
      row.innerHTML =
        '<span class="' +
        (ex ? "tp-state-dot excluded" : "tp-state-dot") +
        '" title="' +
        escapeAttr(ex ? "Excluded=true" : "Excluded=false") +
        '">' +
        escapeHtml(ex ? "-" : "") +
        "</span>" +
        '<div class="tp-cell-name">' +
        escapeHtml(it.name || "") +
        "</div>" +
        '<div class="tp-cell-desc muted">' +
        escapeHtml(desc) +
        "</div>" +
        '<div class="tp-cell-used muted">' +
        escapeHtml(lu) +
        "</div>" +
        '<div class="tp-cell-mod">' +
        escapeHtml(lm) +
        "</div>" +
        '<div class="tp-cell-action">' +
        '<button type="button" class="btn-tp-expand" aria-expanded="false" title="展开/折叠详情">▶</button>' +
        "</div>";
      box.appendChild(row);
    });
  }

  function renderTransportDetail(t) {
    var inner = $("tp-detail-inner");
    if (!inner) return;
    inner.innerHTML = "";
    if (!t || !Object.keys(t).length) {
      inner.innerHTML = '<p class="muted">无详情数据</p>';
      return;
    }
    var form = document.createElement("div");
    form.className = "tp-detail-form";
    (t.scalars || []).forEach(function (row) {
      var tag = row.tag || "";
      var title = tpScalarTitleZh(tag, row.label);
      var disp = tpDisplayScalarValue(tag, row.value);
      var r = document.createElement("div");
      r.className = "tp-dl-row";
      r.innerHTML =
        "<label>" + escapeHtml(title) + "</label>" +
        '<div class="tp-dl-val" tabindex="0">' +
        escapeHtml(disp) +
        "</div>";
      form.appendChild(r);
    });
    inner.appendChild(form);
    (t.sections || []).forEach(function (sec) {
      var det = document.createElement("details");
      det.className = "tp-field-block";
      det.open = true;
      var sm = document.createElement("summary");
      sm.textContent = sec.title || sec.id || "字段列表";
      det.appendChild(sm);
      var rows = sec.fields || [];
      if (!rows.length) {
        var empty = document.createElement("p");
        empty.className = "muted tp-field-empty";
        empty.textContent = "（无字段）";
        det.appendChild(empty);
      } else {
        var tbl = document.createElement("table");
        tbl.className = "data-table tp-field-table";
        tbl.innerHTML = "<thead><tr><th>名称</th><th>标签</th></tr></thead><tbody></tbody>";
        var tb = tbl.querySelector("tbody");
        rows.forEach(function (fr) {
          var tr = document.createElement("tr");
          tr.innerHTML =
            "<td>" + escapeHtml(fr.name || "") + "</td><td>" + escapeHtml(fr.label || "") + "</td>";
          tb.appendChild(tr);
        });
        det.appendChild(tbl);
      }
      inner.appendChild(det);
    });
  }

  async function refreshTransportsPage() {
    transportsDetailCache = Object.create(null);
    resetTransportsDetailUi();
    var meta = $("tp-meta");
    var data = await fetchJson("/api/settings/transports");
    if (!data) {
      renderTransportsList([]);
      if (meta) meta.textContent = "";
      setBanner("tp-banner", "无应答", "err");
      return;
    }
    renderTransportsList(data.items || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t + " · 共 " + ((data.items && data.items.length) || 0) + " 条";
    if (data.ok) setBanner("tp-banner", "", "");
    else setBanner("tp-banner", (data && data.error) || "查询失败", "err");
  }

  async function toggleTransportDetail(key, rowEl, btn) {
    if (!key) return;
    var detWrap = $("tp-detail");
    var body = $("tp-body");
    var inner = $("tp-detail-inner");
    if (transportsExpandedKey === key && detWrap && !detWrap.classList.contains("hidden")) {
      transportsExpandedKey = null;
      if (detWrap) detWrap.classList.add("hidden");
      if (inner) inner.innerHTML = "";
      if (body) body.classList.remove("tp-body--split");
      if (rowEl) rowEl.classList.remove("tp-row--active");
      if (btn) {
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "▶";
      }
      return;
    }
    document.querySelectorAll("#tp-list .tp-row.tp-row--active").forEach(function (r) {
      r.classList.remove("tp-row--active");
    });
    document.querySelectorAll("#tp-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
    transportsExpandedKey = key;
    if (rowEl) rowEl.classList.add("tp-row--active");
    if (btn) {
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "▼";
    }
    if (detWrap) detWrap.classList.remove("hidden");
    if (body) body.classList.add("tp-body--split");
    if (inner) inner.innerHTML = '<p class="muted">加载中…</p>';
    var tjson = transportsDetailCache[key];
    if (!tjson) {
      var url = "/api/settings/transport?key=" + encodeURIComponent(key);
      var d = await fetchJson(url);
      if (!d || !d.ok) {
        if (inner) {
          inner.innerHTML =
            '<p class="muted">' +
            escapeHtml((d && d.error) || "加载详情失败") +
            "</p>";
        }
        setBanner("tp-banner", (d && d.error) || "加载详情失败", "err");
        return;
      }
      tjson = d.transport || {};
      transportsDetailCache[key] = tjson;
    }
    setBanner("tp-banner", "", "");
    renderTransportDetail(tjson);
  }

  function resetMethodsDetailUi() {
    methodsExpandedKey = null;
    var body = $("md-body");
    if (body) body.classList.remove("md-body--split");
    var det = $("md-detail");
    if (det) det.classList.add("hidden");
    var inner = $("md-detail-inner");
    if (inner) inner.innerHTML = "";
    document.querySelectorAll("#md-list .md-row.md-row--active").forEach(function (r) {
      r.classList.remove("md-row--active");
    });
    document.querySelectorAll("#md-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
  }

  function mdListDatetimeHtml(raw) {
    var s = (raw && String(raw).trim()) || "";
    if (!s || s === "—") return '<span class="muted">—</span>';
    var sp = s.indexOf(" ");
    if (sp > 0) {
      return (
        '<span class="md-dt-stack">' +
        '<span class="md-dt-date">' +
        escapeHtml(s.slice(0, sp)) +
        "</span>" +
        '<span class="md-dt-time">' +
        escapeHtml(s.slice(sp + 1)) +
        "</span></span>"
      );
    }
    return escapeHtml(s);
  }

  function mdScalarTitleZh(tag, fallbackLabel) {
    var m = {
      Name: "名称",
      Description: "说明",
      LastUsed: "上次使用",
      LastModified: "上次修改时间",
      Excluded: "已排除"
    };
    return (tag && m[tag]) || (fallbackLabel && String(fallbackLabel).trim()) || tag || "—";
  }

  function mdDisplayScalarValue(tag, raw) {
    var v = raw == null ? "" : String(raw).trim();
    if ((tag || "").trim() === "Excluded") {
      var lo = v.toLowerCase();
      if (lo === "true") return "是";
      if (lo === "false") return "否";
    }
    if (!v) return "—";
    return v;
  }

  function renderMethodsList(items) {
    var box = $("md-list");
    if (!box) return;
    box.innerHTML = "";
    if (!items || !items.length) {
      box.innerHTML = '<p class="muted tp-list-empty">暂无方法</p>';
      return;
    }
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "tp-row md-row";
      row.dataset.key = it.key || "";
      var ex = !!it.excluded;
      var desc = (it.description && String(it.description).trim()) || "—";
      row.innerHTML =
        '<span class="' +
        (ex ? "tp-state-dot md-state-dot excluded" : "tp-state-dot md-state-dot") +
        '" title="' +
        escapeAttr(ex ? "Excluded=true" : "Excluded=false") +
        '">' +
        escapeHtml(ex ? "-" : "") +
        "</span>" +
        '<div class="tp-cell-name">' +
        escapeHtml(it.name || "") +
        "</div>" +
        '<div class="tp-cell-desc muted">' +
        escapeHtml(desc) +
        "</div>" +
        '<div class="tp-cell-used">' +
        mdListDatetimeHtml(it.lastUsed) +
        "</div>" +
        '<div class="tp-cell-mod">' +
        mdListDatetimeHtml(it.lastModified) +
        "</div>" +
        '<div class="tp-cell-action">' +
        '<button type="button" class="btn-tp-expand" aria-expanded="false" title="展开/折叠详情">▶</button>' +
        "</div>";
      box.appendChild(row);
    });
  }

  function appendMethodFields(parent, fields) {
    if (!fields || !fields.length) return;
    var form = document.createElement("div");
    form.className = "tp-detail-form";
    fields.forEach(function (f) {
      var title = (f.label && String(f.label).trim()) || f.id || "—";
      var disp = (f.value && String(f.value).trim()) || "—";
      var r = document.createElement("div");
      r.className = "tp-dl-row";
      r.innerHTML =
        "<label>" + escapeHtml(title) + "</label>" +
        '<div class="tp-dl-val" tabindex="0">' +
        escapeHtml(disp) +
        "</div>";
      form.appendChild(r);
    });
    parent.appendChild(form);
  }

  function renderMethodBlock(block, depth) {
    var det = document.createElement("details");
    det.className = "md-method-block" + (depth > 0 ? " md-method-block--nested" : "");
    det.open = depth < 1;
    var sm = document.createElement("summary");
    var title = (block.label && String(block.label).trim()) || block.id || block.kind || "—";
    if (block.kind === "range" && block.label) {
      title = "Range · " + block.label;
    }
    sm.textContent = title;
    det.appendChild(sm);
    appendMethodFields(det, block.fields || []);
    (block.children || []).forEach(function (ch) {
      if (ch.kind === "sets") {
        var note = document.createElement("p");
        note.className = "muted md-sets-note";
        var parts = (ch.sets || []).map(function (s) {
          var k = s.key || "—";
          var n = s.replicateCount != null ? s.replicateCount : 0;
          return k + "（" + n + " 条 Replicate）";
        });
        note.textContent = parts.length ? "关联 Set：" + parts.join("；") : "（无关联 Set）";
        det.appendChild(note);
        return;
      }
      det.appendChild(renderMethodBlock(ch, depth + 1));
    });
    return det;
  }

  function renderMethodDetail(m) {
    var inner = $("md-detail-inner");
    if (!inner) return;
    inner.innerHTML = "";
    if (!m || !Object.keys(m).length) {
      inner.innerHTML = '<p class="muted">无详情数据</p>';
      return;
    }
    var form = document.createElement("div");
    form.className = "tp-detail-form";
    (m.scalars || []).forEach(function (row) {
      var tag = row.tag || "";
      var title = mdScalarTitleZh(tag, row.label);
      var disp = mdDisplayScalarValue(tag, row.value);
      var r = document.createElement("div");
      r.className = "tp-dl-row";
      r.innerHTML =
        "<label>" + escapeHtml(title) + "</label>" +
        '<div class="tp-dl-val" tabindex="0">' +
        escapeHtml(disp) +
        "</div>";
      form.appendChild(r);
    });
    inner.appendChild(form);
    (m.sections || []).forEach(function (sec) {
      inner.appendChild(renderMethodBlock(sec, 0));
    });
  }

  async function refreshMethodsPage() {
    methodsDetailCache = Object.create(null);
    resetMethodsDetailUi();
    var meta = $("md-meta");
    var data = await fetchJson("/api/settings/methods");
    if (!data) {
      renderMethodsList([]);
      if (meta) meta.textContent = "";
      setBanner("md-banner", "无应答", "err");
      return;
    }
    renderMethodsList(data.items || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t + " · 共 " + ((data.items && data.items.length) || 0) + " 条";
    if (data.ok) setBanner("md-banner", "", "");
    else setBanner("md-banner", (data && data.error) || "查询失败", "err");
  }

  async function toggleMethodDetail(key, rowEl, btn) {
    if (!key) return;
    var detWrap = $("md-detail");
    var body = $("md-body");
    var inner = $("md-detail-inner");
    if (methodsExpandedKey === key && detWrap && !detWrap.classList.contains("hidden")) {
      methodsExpandedKey = null;
      if (detWrap) detWrap.classList.add("hidden");
      if (inner) inner.innerHTML = "";
      if (body) body.classList.remove("md-body--split");
      if (rowEl) rowEl.classList.remove("md-row--active");
      if (btn) {
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "▶";
      }
      return;
    }
    document.querySelectorAll("#md-list .md-row.md-row--active").forEach(function (r) {
      r.classList.remove("md-row--active");
    });
    document.querySelectorAll("#md-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
    methodsExpandedKey = key;
    if (rowEl) rowEl.classList.add("md-row--active");
    if (btn) {
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "▼";
    }
    if (detWrap) detWrap.classList.remove("hidden");
    if (body) body.classList.add("md-body--split");
    if (inner) inner.innerHTML = '<p class="muted">加载中…</p>';
    var mjson = methodsDetailCache[key];
    if (!mjson) {
      var url = "/api/settings/method?key=" + encodeURIComponent(key);
      var d = await fetchJson(url);
      if (!d || !d.ok) {
        if (inner) {
          inner.innerHTML =
            '<p class="muted">' +
            escapeHtml((d && d.error) || "加载详情失败") +
            "</p>";
        }
        setBanner("md-banner", (d && d.error) || "加载详情失败", "err");
        return;
      }
      mjson = d.method || {};
      methodsDetailCache[key] = mjson;
    }
    setBanner("md-banner", "", "");
    renderMethodDetail(mjson);
  }

  function resetStandardsDetailUi() {
    standardsExpandedKey = null;
    var body = $("st-body");
    if (body) body.classList.remove("st-body--split");
    var det = $("st-detail");
    if (det) det.classList.add("hidden");
    var inner = $("st-detail-inner");
    if (inner) inner.innerHTML = "";
    document.querySelectorAll("#st-list .st-row.st-row--active").forEach(function (r) {
      r.classList.remove("st-row--active");
    });
    document.querySelectorAll("#st-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
  }

  function stPctCell(raw) {
    var s = (raw && String(raw).trim()) || "";
    return s ? escapeHtml(s) : '<span class="muted">—</span>';
  }

  function stScalarTitleZh(tag, fallbackLabel) {
    var m = {
      Name: "名称",
      Description: "说明",
      LastUsed: "上次使用",
      LastModified: "上次修改时间",
      Excluded: "已排除",
      GasDoseType: "气体剂量类型",
      GasDoseCycles: "剂量次数"
    };
    return (tag && m[tag]) || (fallbackLabel && String(fallbackLabel).trim()) || tag || "—";
  }

  function stDisplayScalarValue(tag, raw) {
    var v = raw == null ? "" : String(raw).trim();
    if ((tag || "").trim() === "Excluded") {
      var lo = v.toLowerCase();
      if (lo === "true") return "是";
      if (lo === "false") return "否";
    }
    if ((tag || "").trim() === "GasDoseType" && v === "None") return "无";
    if (!v || v.indexOf("0001") >= 0) return "—";
    return v;
  }

  function renderStandardsList(items) {
    var box = $("st-list");
    if (!box) return;
    box.innerHTML = "";
    if (!items || !items.length) {
      box.innerHTML = '<p class="muted tp-list-empty">暂无标样</p>';
      return;
    }
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "st-row";
      row.dataset.key = it.key || "";
      var ex = !!it.excluded;
      var desc = (it.description && String(it.description).trim()) || "—";
      row.innerHTML =
        '<span class="' +
        (ex ? "tp-state-dot st-state-dot excluded" : "tp-state-dot st-state-dot") +
        '" title="' +
        escapeAttr(ex ? "Excluded=true" : "Excluded=false") +
        '">' +
        escapeHtml(ex ? "-" : "") +
        "</span>" +
        '<div class="tp-cell-name">' +
        escapeHtml(it.name || "") +
        "</div>" +
        '<div class="tp-cell-desc muted">' +
        escapeHtml(desc) +
        "</div>" +
        '<div class="st-cell-carbon">' +
        stPctCell(it.carbon) +
        "</div>" +
        '<div class="st-cell-sulfur">' +
        stPctCell(it.sulfur) +
        "</div>" +
        '<div class="tp-cell-mod">' +
        mdListDatetimeHtml(it.lastModified) +
        "</div>" +
        '<div class="tp-cell-action">' +
        '<button type="button" class="btn-tp-expand" aria-expanded="false" title="展开/折叠详情">▶</button>' +
        "</div>";
      box.appendChild(row);
    });
  }

  function renderStandardDetail(s) {
    var inner = $("st-detail-inner");
    if (!inner) return;
    inner.innerHTML = "";
    if (!s || !Object.keys(s).length) {
      inner.innerHTML = '<p class="muted">无详情数据</p>';
      return;
    }
    var form = document.createElement("div");
    form.className = "tp-detail-form";
    (s.scalars || []).forEach(function (row) {
      var tag = row.tag || "";
      var title = stScalarTitleZh(tag, row.label);
      var disp = stDisplayScalarValue(tag, row.value);
      var r = document.createElement("div");
      r.className = "tp-dl-row";
      r.innerHTML =
        "<label>" + escapeHtml(title) + "</label>" +
        '<div class="tp-dl-val" tabindex="0">' +
        escapeHtml(disp) +
        "</div>";
      form.appendChild(r);
    });
    inner.appendChild(form);
    (s.analytes || []).forEach(function (a) {
      var det = document.createElement("details");
      det.className = "md-method-block";
      det.open = true;
      var sm = document.createElement("summary");
      sm.textContent = (a.label && String(a.label).trim()) || a.key || "Analyte";
      det.appendChild(sm);
      (a.fields || []).forEach(function (f) {
        var r = document.createElement("div");
        r.className = "tp-dl-row";
        var title = (f.label && String(f.label).trim()) || f.tag || "—";
        var disp = (f.display && String(f.display).trim()) || f.value || "—";
        r.innerHTML =
          "<label>" + escapeHtml(title) + "</label>" +
          '<div class="tp-dl-val" tabindex="0">' +
          escapeHtml(disp) +
          "</div>";
        det.appendChild(r);
      });
      inner.appendChild(det);
    });
  }

  async function refreshStandardsPage() {
    standardsDetailCache = Object.create(null);
    resetStandardsDetailUi();
    var meta = $("st-meta");
    var data = await fetchJson("/api/settings/standards");
    if (!data) {
      renderStandardsList([]);
      if (meta) meta.textContent = "";
      setBanner("st-banner", "无应答", "err");
      return;
    }
    renderStandardsList(data.items || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t + " · 共 " + ((data.items && data.items.length) || 0) + " 条";
    if (data.ok) setBanner("st-banner", "", "");
    else setBanner("st-banner", (data && data.error) || "查询失败", "err");
  }

  async function toggleStandardDetail(key, rowEl, btn) {
    if (!key) return;
    var detWrap = $("st-detail");
    var body = $("st-body");
    var inner = $("st-detail-inner");
    if (standardsExpandedKey === key && detWrap && !detWrap.classList.contains("hidden")) {
      standardsExpandedKey = null;
      if (detWrap) detWrap.classList.add("hidden");
      if (inner) inner.innerHTML = "";
      if (body) body.classList.remove("st-body--split");
      if (rowEl) rowEl.classList.remove("st-row--active");
      if (btn) {
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "▶";
      }
      return;
    }
    document.querySelectorAll("#st-list .st-row.st-row--active").forEach(function (r) {
      r.classList.remove("st-row--active");
    });
    document.querySelectorAll("#st-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
    standardsExpandedKey = key;
    if (rowEl) rowEl.classList.add("st-row--active");
    if (btn) {
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "▼";
    }
    if (detWrap) detWrap.classList.remove("hidden");
    if (body) body.classList.add("st-body--split");
    if (inner) inner.innerHTML = '<p class="muted">加载中…</p>';
    var sjson = standardsDetailCache[key];
    if (!sjson) {
      var url = "/api/settings/standard?key=" + encodeURIComponent(key);
      var d = await fetchJson(url);
      if (!d || !d.ok) {
        if (inner) {
          inner.innerHTML =
            '<p class="muted">' +
            escapeHtml((d && d.error) || "加载详情失败") +
            "</p>";
        }
        setBanner("st-banner", (d && d.error) || "加载详情失败", "err");
        return;
      }
      sjson = d.standard || {};
      standardsDetailCache[key] = sjson;
    }
    setBanner("st-banner", "", "");
    renderStandardDetail(sjson);
    if (rowEl && (sjson.carbon || sjson.sulfur)) {
      var cEl = rowEl.querySelector(".st-cell-carbon");
      var sEl = rowEl.querySelector(".st-cell-sulfur");
      if (cEl && sjson.carbon) cEl.innerHTML = stPctCell(sjson.carbon);
      if (sEl && sjson.sulfur) sEl.innerHTML = stPctCell(sjson.sulfur);
    }
  }

  async function saveGatewaySettings() {
    var b = lastGwSettings || {};
    function pint(id, fb) {
      var el = $(id);
      var v = parseInt(el && el.value, 10);
      if (isNaN(v)) return fb != null && fb !== "" ? fb : 1;
      return v;
    }
    function pstr(id, fb) {
      var el = $(id);
      if (!el) return fb != null ? String(fb) : "";
      return el.value != null ? String(el.value) : fb != null ? String(fb) : "";
    }
    var body = {
      tcpListenHost: pstr("sett-tcp-host", b.tcpListenHost),
      tcpListenPort: pint("sett-tcp-port", b.tcpListenPort),
      webListenHost: pstr("sett-web-host", b.webListenHost),
      webListenPort: pint("sett-web-port", b.webListenPort),
      upstreamHost: pstr("sett-up-host", b.upstreamHost),
      upstreamPort: pint("sett-up-port", b.upstreamPort),
      webUser: pstr("sett-web-user", b.webUser),
      privilegedAddSamplesHost: pstr("sett-priv-host", b.privilegedAddSamplesHost),
      queueMax: pint("sett-queue-max", b.queueMax),
      persistToConfigFile: $("sett-persist-file") && $("sett-persist-file").checked
    };
    var pw = $("sett-web-password") && $("sett-web-password").value;
    if (pw) body.webPassword = pw;
    var out = await fetchJson("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json;charset=utf-8" },
      body: JSON.stringify(body)
    });
    var pre = $("modal-gw-result");
    if (pre) {
      pre.textContent = JSON.stringify(out, null, 2);
      show(pre, true);
    }
    if (out && out.notes && out.notes.length) {
      if (pre) pre.textContent = out.notes.join("\n") + "\n\n" + pre.textContent;
    }
    if (out && (out.restartRequired || (out.notes && out.notes.length))) {
      setBanner("queue-banner", (out.notes && out.notes[0]) || "部分设置需重启进程后生效。", "warn");
    } else if (out && out.ok) {
      setBanner("queue-banner", "", "");
    }
    if (out && out.ok) {
      await loadConfig();
      await pollGatewayStatus();
      await refreshInstrumentVersionSummary();
    }
  }

  async function loadConfig() {
    var c = await fetchJson("/api/config");
    if (c && c.ok) {
      var ft = $("footer-tcp");
      var fu = $("footer-user");
      var fua = $("footer-upstream-addr");
      if (ft) ft.textContent = c.tcpListen ? ("本地网关 " + c.tcpListen) : "本地网关 —";
      if (fu) fu.textContent = c.webUser ? ("用户 " + c.webUser) : "用户 —";
      if (fua) fua.textContent = "上游 " + (c.upstream || "—");
      if (!c.hasWebCredentials) {
        setBanner(
          "queue-banner",
          "未配置网关 --web-user / --web-password 时：网页发送到仪器、环境参数拉数可能失败。",
          "warn"
        );
        setBanner(
          "env-banner",
          "未配置 --web-user / --web-password 时无法从仪器拉取 Ambients。",
          "warn"
        );
        setBanner(
          "sets-banner",
          "未配置 --web-user / --web-password 时无法查询 Sets / SetReps / RepPlot / RepDetail / Status / Transports（独立 TCP + Logon）。",
          "warn"
        );
        setBanner(
          "counters-banner",
          "未配置 --web-user / --web-password 时无法查询 Counters / Counter 详情（独立 TCP + Logon）。",
          "warn"
        );
        setBanner(
          "dio-banner",
          "未配置 --web-user / --web-password 时无法查询 Solenoids / Switches。",
          "warn"
        );
      } else {
        setBanner("queue-banner", "", "");
        setBanner("env-banner", "", "");
        setBanner("sets-banner", "", "");
        setBanner("counters-banner", "", "");
        setBanner("dio-banner", "", "");
      }
      applyRcsFromPayload(c);
      applyQueueFooterFromPayload(c);
      await refreshInstrumentVersionSummary();
    }
  }

  function renderQueue(items) {
    var tb = $("queue-tbody");
    tb.innerHTML = "";
    if (!items || !items.length) {
      var tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="7" style="color:#888;padding:1rem">队列为空</td>';
      tb.appendChild(tr);
      return;
    }
    items.forEach(function (it) {
      var tr = document.createElement("tr");
      var xmlEsc = (it.xml || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      tr.innerHTML =
        '<td class="col-check"><input type="checkbox" class="qsel" value="' +
        escapeAttr(it.id) +
        '"/></td>' +
        "<td><code>" + escapeHtml(it.id) + "</code></td>" +
        "<td>" + escapeHtml(it.sampleName || "") + "</td>" +
        "<td>" + escapeHtml(it.sampleDescription || "") + "</td>" +
        "<td>" + escapeHtml(it.receivedAtText || "") + "</td>" +
        "<td>" + escapeHtml(it.peer || "") + "</td>" +
        '<td><details class="xml-fold"><summary>展开 XML</summary><pre>' +
        xmlEsc +
        "</pre></details></td>";
      tb.appendChild(tr);
    });
    $("queue-check-all").checked = false;
  }

  function escapeHtml(s) {
    if (!s) return "";
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  function iiFirstChildEl(parent, tagName) {
    var t = String(tagName || "").toUpperCase();
    for (var i = 0; i < parent.children.length; i++) {
      var tn = (parent.children[i].tagName || "").toUpperCase();
      if (tn === t) return parent.children[i];
    }
    return null;
  }

  function iiSectionTitle(text) {
    return '<h3 class="ii-section-title">' + escapeHtml(text) + "</h3>";
  }

  function iiTableTwoCol(h1, h2, rows) {
    var sb =
      '<table class="data-table ii-table"><thead><tr><th>' +
      escapeHtml(h1) +
      "</th><th>" +
      escapeHtml(h2) +
      "</th></tr></thead><tbody>";
    rows.forEach(function (r) {
      sb +=
        "<tr><td>" +
        escapeHtml(r.c1) +
        "</td><td>" +
        escapeHtml(r.c2) +
        "</td></tr>";
    });
    sb += "</tbody></table>";
    return sb;
  }

  function buildInstrumentInfoTablesHtml(xml) {
    var raw = (xml || "").trim();
    if (!raw) return '<p class="modal-ii-err">无数据</p>';
    var parser = new DOMParser();
    var doc = parser.parseFromString(raw, "text/xml");
    if (doc.querySelector("parsererror")) {
      return '<p class="modal-ii-err">' + escapeHtml("XML 解析失败") + "</p>";
    }
    var root = doc.documentElement;
    if (!root || (root.tagName || "").toUpperCase() !== "INSTRUMENTINFO") {
      return '<p class="modal-ii-err">' + escapeHtml("根节点不是 InstrumentInfo") + "</p>";
    }
    var parts = [];
    var fields = [];
    for (var ci = 0; ci < root.children.length; ci++) {
      var ch = root.children[ci];
      if ((ch.tagName || "").toUpperCase() === "FIELD") {
        fields.push({ c1: ch.getAttribute("Label") || "", c2: ch.textContent || "" });
      }
    }
    if (fields.length) {
      parts.push(iiSectionTitle("Field"));
      parts.push(iiTableTwoCol("Label", "值", fields));
    }
    var stRoot = iiFirstChildEl(root, "SampleTypes");
    if (stRoot) {
      var stRows = [];
      for (var sj = 0; sj < stRoot.children.length; sj++) {
        var st = stRoot.children[sj];
        if ((st.tagName || "").toUpperCase() === "SAMPLETYPE") {
          stRows.push({ c1: st.getAttribute("Id") || "", c2: st.getAttribute("Label") || "" });
        }
      }
      if (stRows.length) {
        parts.push(iiSectionTitle("SampleTypes"));
        parts.push(iiTableTwoCol("Id", "Label", stRows));
      }
    }
    var anRoot = iiFirstChildEl(root, "Analytes");
    if (anRoot) {
      var anRows = [];
      for (var ak = 0; ak < anRoot.children.length; ak++) {
        var an = anRoot.children[ak];
        if ((an.tagName || "").toUpperCase() === "ANALYTE") {
          anRows.push({ c1: an.getAttribute("Label") || "", c2: an.textContent || "" });
        }
      }
      if (anRows.length) {
        parts.push(iiSectionTitle("Analytes"));
        parts.push(iiTableTwoCol("Label", "值", anRows));
      }
    }
    if (!parts.length) {
      return '<p class="modal-ii-err">无可表格化内容</p>';
    }
    return parts.join("");
  }

  async function refreshQueue() {
    var data = await fetchJson("/api/queue");
    if (data && data.ok) {
      renderQueue(data.items);
      updateFooterQueueCurrentOnly(data.items ? data.items.length : 0);
    } else setBanner("queue-banner", (data && data.error) || "加载队列失败", "err");
  }

  async function sendQueueSelection() {
    var boxes = document.querySelectorAll(".qsel:checked");
    var ids = [];
    boxes.forEach(function (b) { ids.push(b.value); });
    var pre = $("queue-send-result");
    if (!ids.length) {
      show(pre, true);
      pre.textContent = "请先勾选要发送的条目。";
      return;
    }
    var data = await fetchJson("/api/queue/send", {
      method: "POST",
      headers: { "Content-Type": "application/json;charset=utf-8" },
      body: JSON.stringify({ ids: ids }),
    });
    show(pre, true);
    pre.textContent = JSON.stringify(data, null, 2);
    await refreshQueue();
  }

  function renderAmbients(items) {
    var grid = $("ambients-grid");
    grid.innerHTML = "";
    if (!items || !items.length) {
      grid.innerHTML = '<p style="color:#888">无数据</p>';
      return;
    }
    items.forEach(function (a) {
      var card = document.createElement("div");
      card.className = "card" + (a.inWarning === "True" ? " warn" : "");
      var range = "";
      if (a.min || a.max) range = "最小 " + (a.min || "—") + " · 最大 " + (a.max || "—");
      card.innerHTML =
        '<div class="title">' + escapeHtml(a.name || "") + "</div>" +
        '<div class="value">' + escapeHtml(a.value || "—") + "</div>" +
        '<div class="range">' + escapeHtml(range) + "</div>" +
        '<div class="type">' + escapeHtml(a.type || "") + " · " + escapeHtml(a.units || "") + "</div>";
      grid.appendChild(card);
    });
  }

  async function refreshAmbients() {
    var data = await fetchJson("/api/environment/ambients");
    var meta = $("ambients-meta");
    if (data && data.ok) {
      renderAmbients(data.items);
      var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
      meta.textContent = "已更新 " + t + " · " + (data.items ? data.items.length : 0) + " 项";
      setBanner("env-banner", "", "");
    } else {
      meta.textContent = "";
      renderAmbients([]);
      setBanner("env-banner", (data && data.error) || "拉取失败", "err");
    }
  }

  function dioLedClass(kind) {
    if (kind === "warn") return "dio-led dio-led--warn";
    if (kind === "on") return "dio-led dio-led--on";
    return "dio-led dio-led--off";
  }

  function renderDigitalIoSolenoids(items) {
    var el = $("dio-solenoids");
    if (!el) return;
    el.innerHTML = "";
    if (!items || !items.length) {
      el.innerHTML = '<p class="muted" style="padding:0.5rem">无输出项</p>';
      return;
    }
    var safeIco = { valve: 1, vac: 1, pump: 1, fan: 1, plate: 1, brush: 1, lance: 1, light: 1, default: 1 };
    items.forEach(function (it) {
      var on = !!it.on;
      var rawIk = (it.iconKind && String(it.iconKind)) || "default";
      var ik = safeIco[rawIk] ? rawIk : "default";
      var row = document.createElement("div");
      row.className = "dio-card";
      row.innerHTML =
        '<span class="' +
        (on ? "dio-led dio-led--on" : "dio-led dio-led--off") +
        '" title="' +
        escapeAttr(on ? "Set" : "Unset") +
        '"></span>' +
        '<div class="dio-card-body">' +
        '<div class="dio-card-title">' +
        escapeHtml(it.name || it.label || "") +
        "</div>" +
        '<div class="dio-card-row"><span class="dio-ico dio-ico--' +
        escapeHtml(ik) +
        '"></span><span class="dio-id">' +
        escapeHtml(it.label || "") +
        "</span></div></div>";
      el.appendChild(row);
    });
  }

  function renderDigitalIoSwitches(items) {
    var el = $("dio-switches");
    if (!el) return;
    el.innerHTML = "";
    if (!items || !items.length) {
      el.innerHTML = '<p class="muted" style="padding:0.5rem">无输入项</p>';
      return;
    }
    items.forEach(function (it) {
      var dk = (it.displayKind && String(it.displayKind)) || (it.on ? "on" : "off");
      var row = document.createElement("div");
      row.className = "dio-card";
      var lab = (it.label || "").trim();
      var idLine = lab ? "(" + lab + ")" : "";
      row.innerHTML =
        '<span class="' +
        dioLedClass(dk) +
        '" title="' +
        escapeAttr(dk === "warn" ? "Interlock · Unset" : it.on ? "Set" : "Unset") +
        '"></span>' +
        '<div class="dio-card-body">' +
        '<div class="dio-card-title">' +
        escapeHtml(it.name || lab || "") +
        "</div>" +
        (idLine
          ? '<div class="dio-card-row"><span class="dio-id">' + escapeHtml(idLine) + "</span></div>"
          : "") +
        "</div>";
      el.appendChild(row);
    });
  }

  async function refreshDigitalIo() {
    var meta = $("dio-meta");
    var sub = $("dio-output-sub");
    var data = await fetchJson("/api/diagnostic/digital-io");
    if (data) {
      renderDigitalIoSolenoids(data.solenoids || []);
      renderDigitalIoSwitches(data.switches || []);
      if (sub) {
        var vs = (data.valveStateDisplay && String(data.valveStateDisplay).trim()) || "";
        if (vs) sub.textContent = vs;
        else if (data.valveStateError) sub.textContent = "阀门状态不可用";
        else sub.textContent = "—";
      }
      var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
      var nS = data.solenoids ? data.solenoids.length : 0;
      var nW = data.switches ? data.switches.length : 0;
      if (meta) meta.textContent = "已更新 " + t + " · 输出 " + nS + " · 输入 " + nW;
      if (data.ok) {
        if (data.valveStateError) {
          setBanner("dio-banner", "ValveStates: " + data.valveStateError, "warn");
        } else {
          setBanner("dio-banner", "", "");
        }
      } else {
        var parts = [];
        if (data.solenoidsError) parts.push("Solenoids: " + data.solenoidsError);
        if (data.switchesError) parts.push("Switches: " + data.switchesError);
        if (data.valveStateError) parts.push("ValveStates: " + data.valveStateError);
        if (data.error && !parts.length) parts.push(data.error);
        var msg = parts.join(" · ") || "部分或全部请求失败";
        var kind = nS + nW > 0 ? "warn" : "err";
        setBanner("dio-banner", msg, kind);
      }
    } else {
      if (meta) meta.textContent = "";
      if (sub) sub.textContent = "—";
      renderDigitalIoSolenoids([]);
      renderDigitalIoSwitches([]);
      setBanner("dio-banner", "无应答", "err");
    }
  }

  function counterStateSymbol(item) {
    if (item && item.isExpired) return "!";
    if (item && item.excluded) return "-";
    return "";
  }

  function counterStateClass(item) {
    if (item && item.isExpired) return "counter-state-dot expired";
    if (item && item.excluded) return "counter-state-dot excluded";
    return "counter-state-dot";
  }

  var countersDetailCache = Object.create(null);
  var countersExpandedKey = null;

  function resetCountersDetailUi() {
    countersExpandedKey = null;
    var body = $("counters-body");
    if (body) body.classList.remove("counters-body--split");
    var det = $("counters-detail");
    if (det) det.classList.add("hidden");
    var inner = $("counters-detail-inner");
    if (inner) inner.innerHTML = "";
    document.querySelectorAll("#counters-list .counter-row.counter-row--active").forEach(function (r) {
      r.classList.remove("counter-row--active");
    });
    document.querySelectorAll("#counters-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
  }

  function counterInstrDateDisplay(raw) {
    var s = raw == null ? "" : String(raw).trim();
    if (!s) return "—";
    if (s.indexOf("0001") >= 0 || s.indexOf("01/01/0001") === 0) return "—";
    var parts = s.replace(/-/g, "/").split(/\s+/);
    if (parts.length >= 2 && parts[0].indexOf("/") >= 0) {
      var mdys = parts[0].split("/");
      if (mdys.length === 3) {
        var m = parseInt(mdys[0], 10);
        var d = parseInt(mdys[1], 10);
        var y = parseInt(mdys[2], 10);
        if (!isNaN(m) && !isNaN(d) && !isNaN(y)) {
          return y + "/" + m + "/" + d + " " + parts[1];
        }
      }
    }
    return s;
  }

  function counterScalarTitleZh(tag, fallbackLabel) {
    var m = {
      Key: "Key",
      Name: "名称",
      Description: "说明",
      ExpiresIn: "到期说明",
      IsExpired: "是否过期",
      LastUsed: "上次使用",
      LastModified: "上次修改时间",
      Excluded: "已排除",
      Ignore: "忽略",
      CounterType: "计数器类型",
      CountBlanks: "计空白",
      UseInspect: "使用检查阈值",
      InspectCount: "检查计数",
      NumInspectResets: "检查重置次数",
      InspectLimit: "检查上限",
      LastInspectReset: "上次检查重置",
      UsePerform: "使用执行阈值",
      PerformCount: "执行计数",
      NumPerformResets: "执行重置次数",
      PerformLimit: "执行上限",
      LastPerformReset: "上次执行重置"
    };
    return (tag && m[tag]) || (fallbackLabel && String(fallbackLabel).trim()) || tag || "—";
  }

  function counterDisplayScalarValue(tag, raw) {
    var t = (tag || "").trim();
    var lo = String(raw == null ? "" : raw)
      .trim()
      .toLowerCase();
    if (
      t === "Excluded" ||
      t === "Ignore" ||
      t === "IsExpired" ||
      t === "CountBlanks" ||
      t === "UseInspect" ||
      t === "UsePerform"
    ) {
      if (lo === "true") return "是";
      if (lo === "false") return "否";
    }
    if (
      t === "LastUsed" ||
      t === "LastModified" ||
      t === "LastInspectReset" ||
      t === "LastPerformReset"
    ) {
      return counterInstrDateDisplay(raw);
    }
    var v = String(raw == null ? "" : raw).trim();
    return v || "—";
  }

  function renderCounterDetail(c) {
    var inner = $("counters-detail-inner");
    if (!inner) return;
    inner.innerHTML = "";
    if (!c || !(c.scalars && c.scalars.length)) {
      inner.innerHTML = '<p class="muted">无详情数据</p>';
      return;
    }
    var form = document.createElement("div");
    form.className = "tp-detail-form";
    (c.scalars || []).forEach(function (row) {
      var tag = row.tag || "";
      var title = counterScalarTitleZh(tag, row.label);
      var disp = counterDisplayScalarValue(tag, row.value);
      var r = document.createElement("div");
      r.className = "tp-dl-row";
      r.innerHTML =
        "<label>" + escapeHtml(title) + "</label>" +
        '<div class="tp-dl-val" tabindex="0">' +
        escapeHtml(disp) +
        "</div>";
      form.appendChild(r);
    });
    inner.appendChild(form);
  }

  async function toggleCounterDetail(key, rowEl, btn) {
    if (!key) return;
    var detWrap = $("counters-detail");
    var body = $("counters-body");
    var inner = $("counters-detail-inner");
    if (countersExpandedKey === key && detWrap && !detWrap.classList.contains("hidden")) {
      countersExpandedKey = null;
      if (detWrap) detWrap.classList.add("hidden");
      if (inner) inner.innerHTML = "";
      if (body) body.classList.remove("counters-body--split");
      if (rowEl) rowEl.classList.remove("counter-row--active");
      if (btn) {
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "▶";
      }
      return;
    }
    document.querySelectorAll("#counters-list .counter-row.counter-row--active").forEach(function (r) {
      r.classList.remove("counter-row--active");
    });
    document.querySelectorAll("#counters-list .btn-tp-expand").forEach(function (b) {
      b.setAttribute("aria-expanded", "false");
      b.textContent = "▶";
    });
    countersExpandedKey = key;
    if (rowEl) rowEl.classList.add("counter-row--active");
    if (btn) {
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = "▼";
    }
    if (detWrap) detWrap.classList.remove("hidden");
    if (body) body.classList.add("counters-body--split");
    if (inner) inner.innerHTML = '<p class="muted">加载中…</p>';
    var j = countersDetailCache[key];
    if (!j) {
      var url = "/api/instrument/counter?key=" + encodeURIComponent(key);
      var d = await fetchJson(url);
      if (!d || !d.ok) {
        if (inner) {
          inner.innerHTML =
            '<p class="muted">' + escapeHtml((d && d.error) || "加载详情失败") + "</p>";
        }
        setBanner("counters-banner", (d && d.error) || "加载详情失败", "err");
        return;
      }
      j = d.counter || {};
      countersDetailCache[key] = j;
    }
    setBanner("counters-banner", "", "");
    renderCounterDetail(j);
  }

  function renderMaintenanceCounters(items) {
    var box = $("counters-list");
    if (!box) return;
    box.innerHTML = "";
    if (!items || !items.length) {
      box.innerHTML = '<p class="muted" style="padding:0.65rem">暂无维护计数器数据</p>';
      return;
    }
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "counter-row";
      row.dataset.key = it.key || "";
      row.innerHTML =
        '<span class="' +
        counterStateClass(it) +
        '" title="' +
        escapeAttr("Excluded=" + (!!it.excluded) + " · IsExpired=" + (!!it.isExpired)) +
        '">' +
        escapeHtml(counterStateSymbol(it)) +
        "</span>" +
        '<span class="counter-name">' +
        escapeHtml(it.name || "—") +
        "</span>" +
        '<span class="counter-desc">' +
        escapeHtml(it.description || "—") +
        "</span>" +
        '<span class="counter-exp">' +
        escapeHtml(it.expiresIn || "—") +
        "</span>" +
        '<span class="counter-mod">' +
        escapeHtml(it.lastModified || "—") +
        "</span>" +
        '<div class="counter-action">' +
        '<button type="button" class="btn-tp-expand" aria-expanded="false" title="展开/折叠详情">▶</button>' +
        "</div>";
      box.appendChild(row);
    });
  }

  function renderAutomationRows(rows) {
    var box = $("auto-rows");
    if (!box) return;
    box.innerHTML = "";
    if (!rows || !rows.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "暂无自动状态数据";
      box.appendChild(empty);
      return;
    }
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "auto-row";
      row.innerHTML =
        '<span class="auto-label">' + escapeHtml(r.label || "") + "</span>" +
        '<span class="auto-value">' + escapeHtml(r.value || "—") + "</span>";
      box.appendChild(row);
    });
  }

  async function refreshAutomation() {
    var meta = $("auto-meta");
    var data = await fetchJson("/api/instrument/automation-status");
    if (!data) {
      renderAutomationRows([]);
      if (meta) meta.textContent = "";
      setBanner("auto-banner", "无应答", "err");
      return;
    }
    renderAutomationRows(data.rows || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t;
    if (data.ok) setBanner("auto-banner", "", "");
    else setBanner("auto-banner", (data && data.error) || "查询失败", "err");
  }

  function sysParamDisplayZh(field) {
    var d = String((field && field.display) || "").trim();
    var dl = d.toLowerCase();
    if (!d) return "—";
    if (dl === "enabled") return "启用";
    if (dl === "disabled") return "禁用";
    if (dl === "yes") return "是";
    if (dl === "no") return "否";
    if (dl === "gas off") return "气体关闭";
    if (dl.indexOf("conserve") >= 0) return "节省气";
    if (dl.indexOf("最小") >= 0 || dl.indexOf("min") >= 0) return d.replace(/min\.?/i, "分钟");
    return d;
  }

  function sysParamBoolLabels(field) {
    var id = (field && field.id) || "";
    if (id === "AnalyzeCarbon" || id === "AnalyzeSulfur" || id === "DustFilterHeater" || id === "GasDoser") {
      return { off: "禁用", on: "启用" };
    }
    if (id === "RunLeakCheck") {
      return { off: "否", on: "是" };
    }
    if (id === "ShareUsageWithLeco" || id === "ShareUsageWithLECO") {
      return { off: "关", on: "是" };
    }
    var dl = String((field && field.display) || "").toLowerCase();
    if (dl === "enabled" || dl === "disabled") return { off: "禁用", on: "启用" };
    if (dl === "yes" || dl === "no") return { off: "否", on: "是" };
    return { off: "关", on: "开" };
  }

  function sysParamIsOn(field) {
    var dl = String((field && field.display) || "").trim().toLowerCase();
    if (dl === "yes" || dl === "enabled") return true;
    if (dl === "no" || dl === "disabled" || dl === "gas off") return false;
    var rv = String((field && field.rawValue) || "").trim().toLowerCase();
    if (rv === "true") return true;
    if (rv === "false") return false;
    return false;
  }

  function sysParamControlHtml(field) {
    if (!field) return '<span class="sysp-pill muted">—</span>';
    if (field.kind === "bool") {
      var labels = sysParamBoolLabels(field);
      var on = sysParamIsOn(field);
      return (
        '<span class="sysp-toggle" role="group" aria-label="' +
        escapeAttr(field.label || "") +
        '">' +
        '<span class="sysp-toggle-opt sysp-toggle-opt--off' +
        (!on ? " sysp-toggle-opt--on" : "") +
        '">' +
        escapeHtml(labels.off) +
        "</span>" +
        '<span class="sysp-toggle-opt sysp-toggle-opt--on' +
        (on ? " sysp-toggle-opt--on" : "") +
        '">' +
        escapeHtml(labels.on) +
        "</span></span>"
      );
    }
    return '<span class="sysp-pill">' + escapeHtml(sysParamDisplayZh(field)) + "</span>";
  }

  function renderSystemParameters(sections) {
    var box = $("sysp-sections");
    if (!box) return;
    box.innerHTML = "";
    if (!sections || !sections.length) {
      box.innerHTML = '<p class="muted">暂无系统参数</p>';
      return;
    }
    sections.forEach(function (sec, idx) {
      var det = document.createElement("details");
      det.className = "sysp-section";
      det.open = idx < 6;
      var sm = document.createElement("summary");
      sm.textContent = sec.title || sec.id || "—";
      det.appendChild(sm);
      var body = document.createElement("div");
      body.className = "sysp-section-body";
      (sec.fields || []).forEach(function (f) {
        var row = document.createElement("div");
        row.className = "sysp-row";
        var hint = "";
        if (f.id === "AutoCheckForUpdates" || f.id === "AutoCheckSoftwareUpdates") {
          hint = '<div class="sysp-row-hint">无法检查更新。</div>';
        }
        row.innerHTML =
          '<div class="sysp-row-label-wrap">' +
          '<div class="sysp-row-label">' +
          escapeHtml(f.label || f.labelEn || f.id || "—") +
          "</div>" +
          hint +
          "</div>" +
          '<div class="sysp-control">' +
          sysParamControlHtml(f) +
          "</div>";
        body.appendChild(row);
      });
      det.appendChild(body);
      box.appendChild(det);
    });
  }

  async function refreshSystemParameters() {
    var meta = $("sysp-meta");
    var data = await fetchJson("/api/instrument/system-parameters");
    if (!data) {
      renderSystemParameters([]);
      if (meta) meta.textContent = "";
      setBanner("sysp-banner", "无应答", "err");
      return;
    }
    renderSystemParameters(data.sections || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t;
    if (data.ok) setBanner("sysp-banner", "", "");
    else setBanner("sysp-banner", (data && data.error) || "查询失败", "err");
  }

  async function refreshMaintenanceCounters() {
    countersDetailCache = Object.create(null);
    resetCountersDetailUi();
    var meta = $("counters-meta");
    var data = await fetchJson("/api/instrument/counters");
    if (!data) {
      renderMaintenanceCounters([]);
      if (meta) meta.textContent = "";
      setBanner("counters-banner", "无应答", "err");
      return;
    }
    renderMaintenanceCounters(data.items || []);
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t + " · 共 " + ((data.items && data.items.length) || 0) + " 条";
    if (data.ok) setBanner("counters-banner", "", "");
    else setBanner("counters-banner", (data && data.error) || "查询失败", "err");
  }

  function systemCheckItemLabel(st) {
    var s = String(st || "").trim().toLowerCase();
    if (s === "passed") return "已通过";
    if (s === "failed") return "未通过";
    return st || "—";
  }

  function systemCheckBadgeClass(st) {
    var s = String(st || "").trim().toLowerCase();
    if (s === "failed") return "sc-sys-badge sc-sys-badge--fail";
    if (s === "passed") return "sc-sys-badge sc-sys-badge--pass";
    return "sc-sys-badge sc-sys-badge--neutral";
  }

  function systemCheckDotClass(st) {
    var s = String(st || "").trim().toLowerCase();
    if (s === "failed") return "sc-sys-dot sc-sys-dot--fail";
    return "sc-sys-dot";
  }

  function renderStatusCheckElements(rows) {
    var tb = $("sc-elements-tbody");
    if (!tb) return;
    tb.innerHTML = "";
    if (!rows || !rows.length) {
      var tr0 = document.createElement("tr");
      tr0.innerHTML = '<td colspan="2" class="muted">暂无数据</td>';
      tb.appendChild(tr0);
      return;
    }
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(r.key || "") + "</td><td class=\"sc-val\">" + escapeHtml(r.value || "") + "</td>";
      tb.appendChild(tr);
    });
  }

  function renderStatusCheckOdometers(rows) {
    var tb = $("sc-odometers-tbody");
    if (!tb) return;
    tb.innerHTML = "";
    if (!rows || !rows.length) {
      var tr0 = document.createElement("tr");
      tr0.innerHTML = '<td colspan="2" class="muted">暂无数据</td>';
      tb.appendChild(tr0);
      return;
    }
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + escapeHtml(r.type || "") + "</td><td class=\"sc-val\">" + escapeHtml(r.value || "") + "</td>";
      tb.appendChild(tr);
    });
  }

  function renderLeakKvRows(rows) {
    if (!rows || !rows.length) return '<p class="muted leak-empty-body">（无明细字段）</p>';
    return (
      '<div class="leak-kv-list">' +
      rows
        .map(function (row) {
          return (
            '<div class="leak-kv">' +
            "<span>" +
            escapeHtml(row.label || "") +
            "</span><span>" +
            escapeHtml(row.value || "") +
            "</span></div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function renderLeakChecks(leaks) {
    var root = $("sc-leak-root");
    if (!root) return;
    root.innerHTML = "";
    if (!leaks || !leaks.length) {
      root.innerHTML = '<p class="muted">暂无漏气检查项</p>';
      return;
    }
    leaks.forEach(function (lk) {
      var wrap = document.createElement("div");
      wrap.className = "leak-group";
      var title = (lk.label || lk.id || "漏气检查").trim();
      var segs = lk.segments || [];
      var cardsHtml = "";
      if (segs.length) {
        cardsHtml =
          '<div class="leak-cards-row">' +
          segs
            .map(function (seg) {
              var hc = escapeHtml(seg.headerClass || "leak-h-blue");
              return (
                '<div class="leak-card">' +
                '<div class="leak-card-h ' +
                hc +
                '">' +
                escapeHtml(seg.title || "") +
                "</div>" +
                '<div class="leak-card-body">' +
                renderLeakKvRows(seg.rows) +
                "</div></div>"
              );
            })
            .join("") +
          "</div>";
      } else {
        cardsHtml =
          '<div class="leak-cards-row leak-cards-row--empty">' +
          '<div class="leak-card leak-card--placeholder">' +
          '<div class="leak-card-body muted">' +
          "应答中仅有检查项标识，无压力分段或 Result 明细（与仪器 / Include 选项有关）。" +
          "</div></div></div>";
      }
      var exec = (lk.executionDateText || "").trim();
      var foot =
        escapeHtml(title) +
        " 上次执行 " +
        (exec ? escapeHtml(exec) : "—") +
        (lk.summary ? "。（" + escapeHtml(lk.summary) + "）" : "");
      wrap.innerHTML =
        '<h3 class="leak-group-title">' + escapeHtml(title) + "</h3>" + cardsHtml + '<div class="leak-footer">' + foot + "</div>";
      root.appendChild(wrap);
    });
  }

  function renderSystemCheck(sc) {
    var root = $("sc-system-root");
    if (!root) return;
    root.innerHTML = "";
    var items = (sc && sc.items) || [];
    if (!items.length) {
      root.innerHTML = '<p class="muted">暂无系统检查项</p>';
      return;
    }
    var grid = document.createElement("div");
    grid.className = "sc-sys-grid";
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "sc-sys-item";
      var lab = (it.label || it.id || "").trim();
      var stl = String(it.status || "").trim().toLowerCase();
      var sym = stl === "failed" ? "✗" : "✓";
      row.innerHTML =
        '<span class="' +
        systemCheckDotClass(it.status) +
        '" aria-hidden="true"></span>' +
        '<span class="sc-sys-label">' +
        escapeHtml(lab) +
        "</span>" +
        '<span class="' +
        systemCheckBadgeClass(it.status) +
        '"><span class="sc-sys-tick" aria-hidden="true">' +
        sym +
        "</span> " +
        escapeHtml(systemCheckItemLabel(it.status)) +
        "</span>";
      grid.appendChild(row);
    });
    root.appendChild(grid);
    var foot = document.createElement("div");
    foot.className = "sc-sys-footer";
    var total = sc.total != null ? sc.total : items.length;
    var ex = sc.executed != null ? sc.executed : items.length;
    var ps = sc.passed != null ? sc.passed : 0;
    var fs = sc.failed != null ? sc.failed : 0;
    var line1 =
      "系统检查完成，" +
      total +
      " 步长的 " +
      ex +
      " 已执行。" +
      ps +
      " 通过，" +
      fs +
      " 失败。";
    var ed = (sc.executionDateText || "").trim();
    var line2 = "系统检查 上次执行 " + (ed || "—") + "。";
    foot.innerHTML = "<p>" + escapeHtml(line1) + "</p><p>" + escapeHtml(line2) + "</p>";
    root.appendChild(foot);
  }

  async function refreshStatusCheck() {
    var meta = $("sc-meta");
    var data = await fetchJson("/api/diagnostic/status-check");
    if (!data) {
      renderStatusCheckElements([]);
      renderStatusCheckOdometers([]);
      renderLeakChecks([]);
      renderSystemCheck({});
      if (meta) meta.textContent = "";
      setBanner("sc-banner", "无应答", "err");
      return;
    }
    renderStatusCheckElements(data.elements || []);
    renderStatusCheckOdometers(data.odometers || []);
    renderLeakChecks(data.leakChecks || []);
    renderSystemCheck(data.systemCheck || {});
    var t = data.fetchedAt ? new Date(data.fetchedAt * 1000).toLocaleString() : "";
    if (meta) meta.textContent = "已更新 " + t;
    if (data.ok) setBanner("sc-banner", "", "");
    else setBanner("sc-banner", (data && data.error) || "查询失败", "err");
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPollIfNeeded() {
    stopPoll();
    var iv = document.querySelector('input[name="env-refresh"][value="interval"]');
    if (iv && iv.checked) pollTimer = setInterval(refreshAmbients, 5000);
  }

  function setsTbodyHasSetRows() {
    var tb = $("sets-tbody");
    return !!(tb && tb.querySelector(".setpick"));
  }

  function setsPanelHintIsEmpty() {
    var el = $("sets-panel-hint");
    return !el || !String(el.textContent || "").replace(/\s+/g, "").length;
  }

  /**
   * 首次进入分析页时：若 #sets-panel-hint 为空且 Sets 表尚无数据行，则自动执行一次「查询 Sets」。
   * （提示区故意留空，完整说明在 section.panel 的 title 悬停可见。）
   */
  function maybeAutoLoadSetsOnceOnAnalysis() {
    if (analysisAutoSetsOnceDone) return;
    if (setsTbodyHasSetRows()) {
      analysisAutoSetsOnceDone = true;
      return;
    }
    if (setsPanelHintIsEmpty()) {
      void loadSets();
      analysisAutoSetsOnceDone = true;
    }
  }

  var viewIdByName = {
    analysis: "view-analysis",
    environment: "view-environment",
    "digital-io": "view-digital-io",
    "status-check": "view-status-check",
    transports: "view-transports",
    methods: "view-methods",
    standards: "view-standards",
    "maintenance-counters": "view-maintenance-counters",
    automation: "view-automation",
    "system-parameters": "view-system-parameters"
  };

  function closeAllNavMenus() {
    document.querySelectorAll(".nav-group.nav-has-menu").forEach(function (g) {
      g.classList.remove("open");
      var b = g.querySelector(".nav-top");
      if (b) b.setAttribute("aria-expanded", "false");
    });
  }

  function updateNavActiveState(viewName) {
    document.querySelectorAll(".nav-top").forEach(function (btn) {
      var v = btn.getAttribute("data-view");
      var menu = btn.getAttribute("data-menu");
      var active = false;
      if (v === "analysis" && viewName === "analysis") active = true;
      if (
        menu === "diagnostics" &&
        (viewName === "environment" || viewName === "digital-io" || viewName === "status-check")
      )
        active = true;
      if (
        menu === "instrument" &&
        (viewName === "maintenance-counters" || viewName === "automation" || viewName === "system-parameters")
      )
        active = true;
      if (menu === "settings" && (viewName === "transports" || viewName === "methods" || viewName === "standards"))
        active = true;
      btn.classList.toggle("active", active);
    });
    document.querySelectorAll(".nav-sub").forEach(function (s) {
      var v = s.getAttribute("data-view");
      s.classList.toggle("active", !!v && v === viewName);
    });
  }

  function switchView(name) {
    if (!viewIdByName[name]) return;
    Object.keys(viewIdByName).forEach(function (k) {
      var el = $(viewIdByName[k]);
      if (el) el.classList.toggle("hidden", k !== name);
    });

    if (name !== "environment") {
      stopPoll();
    } else {
      refreshAmbients();
      startPollIfNeeded();
    }

    if (name === "analysis") {
      refreshQueue();
      maybeAutoLoadSetsOnceOnAnalysis();
    }

    if (name === "digital-io") {
      void refreshDigitalIo();
    }
    if (name === "status-check") {
      void refreshStatusCheck();
    }
    if (name === "maintenance-counters") {
      void refreshMaintenanceCounters();
    }
    if (name === "automation") {
      void refreshAutomation();
    }
    if (name === "system-parameters") {
      void refreshSystemParameters();
    }
    if (name === "transports") {
      void refreshTransportsPage();
    }
    if (name === "methods") {
      void refreshMethodsPage();
    }
    if (name === "standards") {
      void refreshStandardsPage();
    }

    updateNavActiveState(name);
    closeAllNavMenus();
  }

  document.querySelectorAll(".nav-top").forEach(function (btn) {
    btn.addEventListener("click", function (ev) {
      var action = btn.getAttribute("data-action");
      if (action === "open-instrument-info") {
        ev.stopPropagation();
        closeAllNavMenus();
        void openInstrumentInfoModal();
        return;
      }
      var menu = btn.getAttribute("data-menu");
      var view = btn.getAttribute("data-view");
      if (view === "analysis") {
        ev.stopPropagation();
        closeAllNavMenus();
        switchView("analysis");
        return;
      }
      if (!menu) return;
      ev.stopPropagation();
      var group = btn.closest(".nav-group");
      var wasOpen = group && group.classList.contains("open");
      closeAllNavMenus();
      if (!wasOpen && group) {
        group.classList.add("open");
        btn.setAttribute("aria-expanded", "true");
      }
    });
  });

  document.querySelectorAll(".nav-sub").forEach(function (sub) {
    sub.addEventListener("click", function (ev) {
      ev.stopPropagation();
      closeAllNavMenus();
      var view = sub.getAttribute("data-view");
      if (view) switchView(view);
    });
  });

  document.addEventListener("click", function () {
    closeAllNavMenus();
  });
  var mainNav = document.querySelector(".main-nav");
  if (mainNav) {
    mainNav.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });
  }

  function setStatusStripCollapsed(collapsed) {
    var s = $("instrument-status-strip");
    var btn = $("btn-status-strip-toggle");
    if (!s || !btn) return;
    s.classList.toggle("collapsed", collapsed);
    var exp = !collapsed;
    s.setAttribute("aria-expanded", exp ? "true" : "false");
    btn.setAttribute("aria-expanded", exp ? "true" : "false");
    if (exp) {
      var row = $("status-widgets-row");
      if (row && row.dataset.loaded !== "1") {
        void loadStatusWidgets();
      }
    }
  }

  function widgetGaugeMode(w) {
    var u = (w.units || "").toLowerCase();
    var lab = (w.label || "").toLowerCase();
    if (u.indexOf("毫安") >= 0 || lab.indexOf("电流") >= 0) return "simple";
    return "range";
  }

  function renderStatusWidgets(widgets) {
    var row = $("status-widgets-row");
    var err = $("status-widgets-err");
    if (!row) return;
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    if (!widgets || !widgets.length) {
      row.innerHTML = '<span class="muted">暂无 Widget 数据</span>';
      return;
    }
    row.innerHTML = widgets
      .map(function (w) {
        var mode = widgetGaugeMode(w);
        var gclass = mode === "simple" ? "status-widget-gauge simple" : "status-widget-gauge range";
        if (w.warning) gclass += " warn";
        var labClass =
          mode === "range" ? "status-widget-label accent-orange" : "status-widget-label";
        return (
          '<div class="status-widget" data-wid="' +
          escapeHtml(String(w.id || "")) +
          '">' +
          '<div class="' +
          labClass +
          '">' +
          escapeHtml(w.label || "") +
          '</div>' +
          '<div class="status-widget-panel">' +
          '<div class="' +
          gclass +
          '"></div>' +
          '<div class="status-widget-readout">' +
          '<span class="status-widget-value">' +
          escapeHtml(String(w.value != null && w.value !== "" ? w.value : "—")) +
          '</span>' +
          '<span class="status-widget-units">' +
          escapeHtml(w.units || "") +
          "</span></div></div></div>"
        );
      })
      .join("");
  }

  async function loadStatusWidgets() {
    var err = $("status-widgets-err");
    var row = $("status-widgets-row");
    var data = await fetchJson("/api/instrument/status-widgets");
    if (data && data.ok) {
      renderStatusWidgets(data.widgets || []);
      if (err) err.classList.add("hidden");
      if (row) row.dataset.loaded = "1";
    } else {
      renderStatusWidgets([]);
      if (err) {
        err.textContent = (data && data.error) || "Status 请求失败";
        err.classList.remove("hidden");
      }
    }
  }

  var btnStrip = $("btn-status-strip-toggle");
  if (btnStrip) {
    btnStrip.addEventListener("click", function () {
      var s = $("instrument-status-strip");
      if (!s) return;
      setStatusStripCollapsed(!s.classList.contains("collapsed"));
    });
  }

  function setQueueCacheStripCollapsed(collapsed) {
    var s = $("queue-cache-strip");
    var btn = $("btn-queue-cache-toggle");
    if (!s || !btn) return;
    s.classList.toggle("collapsed", collapsed);
    var exp = !collapsed;
    s.setAttribute("aria-expanded", exp ? "true" : "false");
    btn.setAttribute("aria-expanded", exp ? "true" : "false");
  }

  var btnQueueCache = $("btn-queue-cache-toggle");
  if (btnQueueCache) {
    btnQueueCache.addEventListener("click", function () {
      var s = $("queue-cache-strip");
      if (!s) return;
      setQueueCacheStripCollapsed(!s.classList.contains("collapsed"));
    });
  }
  if ($("btn-status-widgets-refresh")) {
    $("btn-status-widgets-refresh").addEventListener("click", function () {
      var row = $("status-widgets-row");
      if (row) delete row.dataset.loaded;
      void loadStatusWidgets();
    });
  }

  $("btn-refresh-queue").addEventListener("click", refreshQueue);
  $("btn-send-queue").addEventListener("click", sendQueueSelection);
  $("queue-check-all").addEventListener("change", function () {
    var on = $("queue-check-all").checked;
    document.querySelectorAll(".qsel").forEach(function (c) { c.checked = on; });
  });

  $("btn-refresh-ambients").addEventListener("click", refreshAmbients);
  if ($("btn-refresh-counters")) {
    $("btn-refresh-counters").addEventListener("click", function () {
      void refreshMaintenanceCounters();
    });
  }
  if ($("btn-refresh-automation")) {
    $("btn-refresh-automation").addEventListener("click", function () {
      void refreshAutomation();
    });
  }
  if ($("btn-refresh-system-parameters")) {
    $("btn-refresh-system-parameters").addEventListener("click", function () {
      void refreshSystemParameters();
    });
  }
  var ctList = $("counters-list");
  if (ctList) {
    ctList.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".btn-tp-expand");
      if (!btn) return;
      var row = btn.closest(".counter-row");
      if (!row) return;
      var key = row.dataset.key || "";
      void toggleCounterDetail(key, row, btn);
    });
  }
  var btnDio = $("btn-refresh-digital-io");
  if (btnDio) btnDio.addEventListener("click", function () { void refreshDigitalIo(); });
  var btnSc = $("btn-refresh-status-check");
  if (btnSc) btnSc.addEventListener("click", function () { void refreshStatusCheck(); });
  document.querySelectorAll('input[name="env-refresh"]').forEach(function (r) {
    r.addEventListener("change", function () {
      if (!$("view-environment").classList.contains("hidden")) startPollIfNeeded();
    });
  });

  var selectedSetKey = "";
  var selectedRepTag = null;
  var lastReps = [];
  var lastSetsPagination = null;
  var lastAnalyteDefs = [];
  var lastRepAnalyteColumns = [];
  var lastElementStats = [];
  var lastRepPlotAnalytes = [];
  var elemChartInstances = {};
  var lastRepDetailFields = [];
  var repDetailLastError = "";
  var repDetailLoading = false;
  var elemPanelView = {};
  var elementPanelVisible = {};
  /** 分析页是否已做过「无数据时自动查 Sets」一次，避免反复请求。 */
  var analysisAutoSetsOnceDone = false;

  function setSetsBanner(text, kind) {
    setBanner("sets-banner", text, kind);
  }

  function safeDomId(s) {
    return String(s || "x").replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  function setsColspan() {
    return 9 + (lastAnalyteDefs && lastAnalyteDefs.length ? lastAnalyteDefs.length : 0);
  }

  function repsColspan() {
    return 5 + (lastRepAnalyteColumns && lastRepAnalyteColumns.length ? lastRepAnalyteColumns.length : 0);
  }

  function renderSetsThead() {
    var tr = $("sets-thead-row");
    if (!tr) return;
    var parts = [
      '<th class="col-radio">选</th>',
      "<th>SetKey</th>",
      "<th>重复</th>",
      "<th>名称</th>",
      "<th>说明</th>",
      "<th>方法</th>",
    ];
    (lastAnalyteDefs || []).forEach(function (a) {
      var lab = (a.label || a.elementKey || "").trim();
      parts.push("<th>" + escapeHtml(lab ? lab + " Avg" : "Avg") + "</th>");
    });
    parts.push("<th>类型</th>", "<th>状态</th>", "<th>完成/时间</th>");
    tr.innerHTML = parts.join("");
  }

  function renderRepsThead() {
    var tr = $("reps-thead-row");
    if (!tr) return;
    var parts = [
      '<th class="col-radio">选</th>',
      "<th>Tag</th>",
      "<th>质量（样品量 / 值状态）</th>",
      "<th>注释</th>",
    ];
    (lastRepAnalyteColumns || []).forEach(function (c) {
      parts.push("<th>" + escapeHtml(c.label || c.registryId || "") + "</th>");
    });
    parts.push("<th>分析日期</th>");
    tr.innerHTML = parts.join("");
  }

  function updateSetsPaginationButtons(data) {
    var p = data && data.pagination;
    lastSetsPagination = p || null;
    var bn = $("btn-sets-next");
    var bp = $("btn-sets-prev");
    if (bp) bp.disabled = !(p && p.nextOlderStartAt != null);
    if (bn) bn.disabled = !(p && p.prevNewerStartAt != null);
  }

  function renderSetsTbody(items, emptyRowText) {
    var tb = $("sets-tbody");
    tb.innerHTML = "";
    var cs = setsColspan();
    if (!items || !items.length) {
      var hint = emptyRowText || "暂无记录";
      tb.innerHTML =
        '<tr><td colspan="' +
        cs +
        '" style="color:#888;padding:0.75rem">' +
        escapeHtml(hint) +
        "</td></tr>";
      return;
    }
    items.forEach(function (row) {
      var tr = document.createElement("tr");
      var sk = row.setKey || "";
      var avgs = row.analyteAvgs || [];
      var dyn = "";
      avgs.forEach(function (cell) {
        dyn += "<td>" + escapeHtml(cell.value || "") + "</td>";
      });
      tr.innerHTML =
        '<td class="col-radio"><input type="radio" name="setpick" class="setpick"/></td>' +
        "<td><code>" +
        escapeHtml(sk) +
        "</code></td>" +
        "<td>" +
        escapeHtml(row.numReps || "") +
        "</td>" +
        "<td>" +
        escapeHtml(row.name || "") +
        "</td>" +
        "<td>" +
        escapeHtml(row.description || "") +
        "</td>" +
        "<td>" +
        escapeHtml(row.method || "") +
        "</td>" +
        dyn +
        "<td>" +
        escapeHtml(row.sampleType || "") +
        "</td>" +
        "<td>" +
        escapeHtml(row.state || "") +
        "</td>" +
        "<td>" +
        escapeHtml(row.completed || "") +
        "</td>";
      var inp = tr.querySelector(".setpick");
      inp.value = sk;
      inp.addEventListener("change", function () {
        if (inp.checked) onSetSelected(sk);
      });
      tb.appendChild(tr);
    });
  }

  function formatRepQualityCell(rep) {
    var m = (rep.mass || "").trim();
    var q = (rep.quality || "").trim();
    if (m && q) return m + " (" + q + ")";
    if (m) return m;
    if (q) return q;
    return "—";
  }

  function renderRepsTbody(reps) {
    lastReps = reps || [];
    var tb = $("reps-tbody");
    tb.innerHTML = "";
    var cs = repsColspan();
    if (!lastReps.length) {
      tb.innerHTML =
        '<tr><td colspan="' +
        cs +
        '" style="color:#888;padding:0.75rem">无 Replicate（请先查询 Sets 并选中 Set）</td></tr>';
      return;
    }
    lastReps.forEach(function (rep, idx) {
      var tr = document.createElement("tr");
      var tag = rep.tag != null ? String(rep.tag) : "";
      var fld = rep.fields || {};
      var dyn = "";
      (lastRepAnalyteColumns || []).forEach(function (c) {
        var rid = c.registryId || "";
        dyn += "<td>" + escapeHtml(fld[rid] || "") + "</td>";
      });
      tr.innerHTML =
        '<td class="col-radio"><input type="radio" name="reppick" class="reppick"/></td>' +
        "<td><code>" +
        escapeHtml(tag) +
        "</code></td>" +
        "<td>" +
        escapeHtml(formatRepQualityCell(rep)) +
        "</td>" +
        "<td>" +
        escapeHtml(rep.comments || "") +
        "</td>" +
        dyn +
        "<td>" +
        escapeHtml(rep.analysisDate || "") +
        "</td>";
      var inp = tr.querySelector(".reppick");
      inp.value = String(idx);
      inp.addEventListener("change", function () {
        if (inp.checked) onRepSelected(tag);
      });
      tb.appendChild(tr);
    });
  }

  function showSetLevelViz() {
    show($("elem-toggle-bar"), true);
    show($("elem-panels-row"), true);
  }

  function buildElementPanels() {
    var bar = $("elem-toggle-bar");
    var row = $("elem-panels-row");
    if (!bar || !row) return;
    disposeAllElemCharts();
    bar.innerHTML = "";
    row.innerHTML = "";
    elementPanelVisible = {};
    (lastElementStats || []).forEach(function (st) {
      var rid = st.registryId || "";
      var sid = safeDomId(rid);
      elementPanelVisible[sid] = true;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "elem-pill active";
      btn.textContent = st.label || rid;
      btn.setAttribute("data-sid", sid);
      btn.addEventListener("click", function () {
        elementPanelVisible[sid] = !elementPanelVisible[sid];
        btn.classList.toggle("active", elementPanelVisible[sid]);
        var pan = $("elem-panel-" + sid);
        if (pan) {
          show(pan, elementPanelVisible[sid]);
          if (elementPanelVisible[sid]) {
            var inst = elemChartInstances[sid];
            if (inst && !inst.isDisposed()) inst.resize();
          }
        }
      });
      bar.appendChild(btn);
      var u = st.units || "%";
      var title = (st.label || rid) + " ± 1σ (" + u + ")";
      var line2 =
        st.mean != null && st.std != null
          ? Number(st.mean).toFixed(6) + " ± " + Number(st.std).toFixed(6)
          : st.meanPlusMinusSigma || "—";
      var n = st.n != null ? st.n : 0;
      var rsd =
        st.rsdPercent != null && !isNaN(st.rsdPercent) ? Number(st.rsdPercent).toFixed(5) : "—";
      var panel = document.createElement("div");
      panel.className = "elem-panel";
      panel.id = "elem-panel-" + sid;
      panel.innerHTML =
        '<div class="elem-panel-head">' +
        '<span class="elem-panel-title">' +
        escapeHtml(title) +
        '</span><span class="elem-panel-head-actions">' +
        '<button type="button" class="elem-viz-toggle" data-sid="' +
        escapeHtml(sid) +
        '" title="在谱图与 RepDetail 元素详情之间切换">详情</button>' +
        '<span class="elem-panel-gear" title="设置">⚙</span></span></div>' +
        '<div class="elem-panel-mean">' +
        escapeHtml(line2) +
        "</div>" +
        '<div class="elem-panel-meta">n = ' +
        n +
        ", RSD(%) = " +
        escapeHtml(String(rsd)) +
        "</div>" +
        '<div class="elem-viz-wrap">' +
        '<div class="elem-plot-stack" id="elem-plot-stack-' +
        sid +
        '">' +
        '<div class="elem-mini-plot" id="elem-chart-' +
        sid +
        '"></div>' +
        '<div class="elem-plot-hint muted hidden" id="elem-plot-hint-' +
        sid +
        '"></div></div>' +
        '<div class="elem-detail-stack hidden" id="elem-detail-stack-' +
        sid +
        '">' +
        '<div class="elem-detail-grid" id="elem-detail-grid-' +
        sid +
        '"></div></div></div>';
      row.appendChild(panel);
    });
  }

  function registryElementToken(st) {
    var r = (st.registryId || "").trim();
    var parts = r.split(/\s+/);
    if (parts[0]) return parts[0].toLowerCase();
    var lb = (st.label || "").trim().split(/\s+/)[0];
    return (lb || "").toLowerCase();
  }

  function detailFieldMatchesElement(field, st) {
    var rid = (field.registryId || "").toLowerCase();
    var lab = (field.label || "").toLowerCase();
    if (rid === "cycle time" || lab === "cycle time") return true;
    var tok = registryElementToken(st);
    if (!tok) return false;
    if (rid.indexOf(tok) === 0) return true;
    if (lab.indexOf(tok) === 0) return true;
    return false;
  }

  function displayRepDetailFieldValue(f) {
    var v = ((f && f.value) || "").trim();
    var u = ((f && f.units) || "").trim();
    if (v && u && v.indexOf(u) < 0) return v + " " + u;
    if (v) return v;
    return ((f && f.rawValue) || "").trim() || "—";
  }

  /** RepDetail 卡片：与元素无关的统一分组顺序（Carbon / Sulfur 一致）。 */
  var DETAIL_FIELD_TYPE_ORDER = [
    "adjusted area",
    "blank area",
    "calibration equation",
    "drift factor",
    "peak height",
    "raw area",
  ];

  function detailFieldNormKey(f) {
    return ((f.registryId || "") + " " + (f.label || "")).toLowerCase();
  }

  /** 大组：Cycle → High → Low → Mass → Range → 其它。 */
  function detailFieldBand(f) {
    var rid = (f.registryId || "").trim().toLowerCase();
    var lab = (f.label || "").trim().toLowerCase();
    var n = rid.replace(/\s+/g, "");
    if (n === "cycletime" || lab === "cycle time") return 0;
    if (n.endsWith("range") || /(^|[\s])range$/i.test(rid)) return 50;
    if (n.endsWith("mass") && n.indexOf("peak") < 0 && lab.indexOf("peak") < 0) return 40;
    if (n.indexOf("high") >= 0 && n.indexOf("low") < 0) return 10;
    if (n.indexOf("low") >= 0 || /\blow\b/i.test(lab)) return 20;
    return 60;
  }

  /** High / Low 组内子类型顺序（两元素相同）。 */
  function detailFieldTypeIndex(f) {
    var hay = detailFieldNormKey(f);
    for (var i = 0; i < DETAIL_FIELD_TYPE_ORDER.length; i++) {
      if (hay.indexOf(DETAIL_FIELD_TYPE_ORDER[i]) >= 0) return i;
    }
    return 80;
  }

  function compareRepDetailFields(a, b) {
    var ba = detailFieldBand(a);
    var bb = detailFieldBand(b);
    if (ba !== bb) return ba - bb;
    if (ba === 10 || ba === 20) {
      var ta = detailFieldTypeIndex(a);
      var tb = detailFieldTypeIndex(b);
      if (ta !== tb) return ta - tb;
    }
    return (a.label || a.registryId || "").localeCompare(
      b.label || b.registryId || "",
      "zh"
    );
  }

  function setElemPanelView(sid, mode) {
    elemPanelView[sid] = mode;
    var ps = $("elem-plot-stack-" + sid);
    var ds = $("elem-detail-stack-" + sid);
    var btn = document.querySelector('.elem-viz-toggle[data-sid="' + sid + '"]');
    if (ps) show(ps, mode === "plot");
    if (ds) show(ds, mode === "detail");
    if (btn) btn.textContent = mode === "plot" ? "详情" : "图谱";
    if (mode === "plot") {
      var inst = elemChartInstances[sid];
      if (inst && !inst.isDisposed()) {
        setTimeout(function () {
          inst.resize();
        }, 0);
      }
    }
  }

  function renderAllDetailGrids() {
    (lastElementStats || []).forEach(function (st) {
      var sid = safeDomId(st.registryId || "");
      var grid = $("elem-detail-grid-" + sid);
      if (!grid) return;
      if (selectedRepTag === null || selectedRepTag === undefined || selectedRepTag === "") {
        grid.innerHTML = "";
        return;
      }
      if (repDetailLoading) {
        grid.innerHTML =
          '<div class="elem-detail-card"><div class="t">RepDetail</div><div class="v">加载中…</div></div>';
        return;
      }
      if (repDetailLastError && (!lastRepDetailFields || !lastRepDetailFields.length)) {
        grid.innerHTML =
          '<div class="elem-detail-card elem-detail-card-err"><div class="t">RepDetail</div><div class="v">' +
          escapeHtml(repDetailLastError) +
          "</div></div>";
        return;
      }
      var rows = (lastRepDetailFields || []).filter(function (f) {
        return detailFieldMatchesElement(f, st);
      });
      rows.sort(compareRepDetailFields);
      if (!rows.length) {
        grid.innerHTML =
          '<div class="elem-detail-card"><div class="t">RepDetail</div><div class="v">无该元素明细字段</div></div>';
        return;
      }
      grid.innerHTML = rows
        .map(function (f) {
          var t = f.label || f.registryId || "—";
          var val = displayRepDetailFieldValue(f);
          return (
            '<div class="elem-detail-card"><div class="t">' +
            escapeHtml(t) +
            '</div><div class="v">' +
            escapeHtml(val) +
            "</div></div>"
          );
        })
        .join("");
    });
  }

  async function loadRepDetail() {
    if (!selectedSetKey || selectedRepTag === null || selectedRepTag === undefined) {
      lastRepDetailFields = [];
      repDetailLastError = "";
      repDetailLoading = false;
      renderAllDetailGrids();
      return;
    }
    repDetailLoading = true;
    lastRepDetailFields = [];
    repDetailLastError = "";
    renderAllDetailGrids();
    var q =
      "?set_key=" +
      encodeURIComponent(selectedSetKey) +
      "&tag=" +
      encodeURIComponent(String(selectedRepTag));
    var data = await fetchJson("/api/instrument/rep-detail" + q);
    repDetailLoading = false;
    if (!data || !data.ok) {
      lastRepDetailFields = [];
      repDetailLastError =
        (data && (data.error || data.errorMessage)) || "RepDetail 请求失败";
      renderAllDetailGrids();
      return;
    }
    lastRepDetailFields = data.detailFields || [];
    repDetailLastError = "";
    renderAllDetailGrids();
  }

  function disposeAllElemCharts() {
    Object.keys(elemChartInstances).forEach(function (sid) {
      var inst = elemChartInstances[sid];
      if (inst && !inst.isDisposed()) inst.dispose();
    });
    elemChartInstances = {};
  }

  function getOrCreateElemChart(container) {
    if (!container || typeof echarts === "undefined") return null;
    var existing = echarts.getInstanceByDom(container);
    if (existing) return existing;
    var sid = (container.id || "").replace("elem-chart-", "");
    var chart = echarts.init(container, null, { renderer: "canvas" });
    if (sid) elemChartInstances[sid] = chart;
    return chart;
  }

  function resizeAllElemCharts() {
    Object.keys(elemChartInstances).forEach(function (sid) {
      var inst = elemChartInstances[sid];
      if (inst && !inst.isDisposed()) inst.resize();
    });
  }

  function showChartMessage(container, msg) {
    var chart = getOrCreateElemChart(container);
    if (!chart) return;
    chart.setOption(
      {
        backgroundColor: "#2a2a2e",
        title: {
          text: msg || "—",
          left: "center",
          top: "middle",
          textStyle: { color: "#888", fontSize: 12, fontWeight: "normal" },
        },
      },
      true
    );
  }

  function buildLineChartOption(points, bounds) {
    var xs = points.map(function (p) {
      return p[0];
    });
    var ys = points.map(function (p) {
      return p[1];
    });
    var minX = bounds && bounds.xMin != null ? bounds.xMin : Math.min.apply(null, xs);
    var maxX = bounds && bounds.xMax != null ? bounds.xMax : Math.max.apply(null, xs);
    var dataYMin = Math.min.apply(null, ys);
    var dataYMax = Math.max.apply(null, ys);
    var minY = bounds && bounds.yMin != null ? bounds.yMin : dataYMin;
    var maxY = bounds && bounds.yMax != null ? bounds.yMax : dataYMax;
    if (maxX === minX) maxX = minX + 1;
    var yScale = pickYAxisIntScale(
      Math.min(minY, dataYMin),
      Math.max(maxY, dataYMax, dataYMin + 1e-12)
    );
    var xt = pickXAxisTickSeconds(minX, maxX);
    var endLabel = "结束 " + formatAxisXSeconds(maxX) + " s";
    return {
      backgroundColor: "#2a2a2e",
      animation: false,
      grid: { left: 44, right: 8, top: 28, bottom: 44, containLabel: false },
      title: {
        text: "强度 max " + formatIntensity(dataYMax),
        left: 44,
        top: 2,
        textStyle: { color: "#a0a87a", fontSize: 10, fontWeight: "normal" },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", lineStyle: { color: "#666" } },
        backgroundColor: "rgba(30,30,34,0.95)",
        borderColor: "#45454d",
        textStyle: { color: "#ddd", fontSize: 11 },
        formatter: function (params) {
          var p = params && params[0];
          if (!p || !p.data) return "";
          var xy = p.data;
          return (
            "时间 " +
            formatAxisXSeconds(xy[0]) +
            " s<br/>强度 " +
            formatIntensity(xy[1])
          );
        },
      },
      xAxis: {
        type: "value",
        min: minX,
        max: maxX,
        name: "时间 (s)",
        nameLocation: "middle",
        nameGap: 28,
        nameTextStyle: { color: "#666", fontSize: 9 },
        interval: xt.major,
        axisLine: { lineStyle: { color: "#45454d" } },
        axisTick: { lineStyle: { color: "#45454d" } },
        minorTick: { show: true, splitNumber: Math.max(1, Math.round(xt.major / xt.minor) - 1) },
        axisLabel: {
          color: "#b0b0b8",
          fontSize: 10,
          formatter: function (v) {
            return formatAxisXSeconds(v);
          },
        },
        splitLine: { show: true, lineStyle: { color: "#5c5c66" } },
        minorSplitLine: { show: true, lineStyle: { color: "#45454d" } },
      },
      yAxis: {
        type: "value",
        min: yScale.y0,
        max: yScale.y1,
        interval: yScale.step,
        axisLine: { show: true, lineStyle: { color: "#45454d" } },
        axisTick: { show: true, lineStyle: { color: "#45454d" } },
        axisLabel: {
          color: "#7a7a82",
          fontSize: 10,
          formatter: function (v) {
            return formatYTickLabel(v, yScale.step);
          },
        },
        splitLine: { show: true, lineStyle: { color: "#35353c" } },
      },
      series: [
        {
          type: "line",
          data: points,
          showSymbol: false,
          lineStyle: { color: "#4a9eff", width: 2 },
          itemStyle: { color: "#4a9eff" },
        },
      ],
      graphic: [
        {
          type: "text",
          right: 8,
          bottom: 6,
          style: { text: endLabel, fill: "#9a9aa2", fontSize: 9, textAlign: "right" },
        },
      ],
    };
  }

  function drawLineOnChart(container, points, bounds) {
    if (!container || !points || points.length < 2) return;
    var chart = getOrCreateElemChart(container);
    if (!chart) return;
    chart.setOption(buildLineChartOption(points, bounds), true);
    chart.resize();
  }

  function showChartImage(container, mime, b64) {
    if (!container) return;
    var chart = getOrCreateElemChart(container);
    if (!chart) return;
    var im = new Image();
    im.onload = function () {
      var cw = container.clientWidth || 360;
      var ch = container.clientHeight || 200;
      var r = Math.min(cw / im.width, ch / im.height);
      var w = im.width * r;
      var h = im.height * r;
      chart.setOption(
        {
          backgroundColor: "#2a2a2e",
          graphic: [
            {
              type: "image",
              style: { image: im, width: w, height: h },
              left: (cw - w) / 2,
              top: (ch - h) / 2,
            },
          ],
        },
        true
      );
      chart.resize();
    };
    im.src = mime + ";base64," + b64;
  }

  function formatAxisXSeconds(t) {
    var r = Math.round(t * 100) / 100;
    if (Math.abs(r - Math.round(r)) < 0.02) return String(Math.round(r));
    return r.toFixed(1);
  }

  function formatAxisYInt(y) {
    return String(Math.round(y));
  }

  function formatYTickLabel(y, step) {
    if (step >= 1) return formatAxisYInt(y);
    if (step >= 0.1) return (Math.round(y * 10) / 10).toFixed(1);
    if (step >= 0.01) return (Math.round(y * 100) / 100).toFixed(2);
    return formatIntensity(y);
  }

  /** 强度等物理量：整数或有限位小数，便于读 max 标注。 */
  function formatIntensity(v) {
    if (v == null || isNaN(v)) return "—";
    var a = Math.abs(v);
    if (a >= 1000) return Math.round(v).toString();
    if (a >= 100) return v.toFixed(1);
    if (a >= 10) return v.toFixed(2);
    if (a >= 1) return v.toFixed(3);
    return v.toFixed(4);
  }

  /** 根据数据最大值选纵轴整数步长与上下界（各图独立）。 */
  function pickYAxisIntScale(yDataMin, yDataMax) {
    var lo = Math.min(yDataMin, 0);
    var hi = Math.max(yDataMax, lo + 1e-12);
    var span = hi - lo;
    var target = 5;
    var raw = span / target;
    if (raw <= 0 || !isFinite(raw)) raw = 1;
    var pow10 = Math.pow(10, Math.floor(Math.log10(raw)));
    var r = raw / pow10;
    var step;
    if (r <= 1) step = pow10;
    else if (r <= 2) step = 2 * pow10;
    else if (r <= 5) step = 5 * pow10;
    else step = 10 * pow10;
    if (!isFinite(step) || step <= 0) step = span / 4 || 1;
    var y0 = Math.floor(lo / step) * step;
    var y1 = Math.ceil(hi / step) * step;
    if (y1 <= hi) y1 += step;
    return { y0: y0, y1: y1, step: step };
  }

  /** 横轴：5s 细刻度、10s 数字；刻度过密时加倍。结束时间 maxX 必标数字。 */
  function pickXAxisTickSeconds(minX, maxX) {
    var minor = 5;
    var major = 10;
    var span = maxX - minX || 1;
    while (span / minor > 45) {
      minor *= 2;
      major *= 2;
    }
    var start = Math.floor(minX / minor) * minor;
    var end = Math.ceil(maxX / minor) * minor;
    return { minor: minor, major: major, start: start, end: end };
  }

  function findPlotSeriesForRegistry(registryId, panelLabel) {
    var rid = (registryId || "").toLowerCase();
    var pl = (panelLabel || "").toLowerCase();
    var list = lastRepPlotAnalytes || [];
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      var cr = (s.concentrationRegistryId || "").toLowerCase();
      if (cr && rid === cr) return s;
    }
    for (var k = 0; k < list.length; k++) {
      var u = list[k];
      var lb = (u.label || u.analyteKey || "").toLowerCase();
      if (pl && lb && pl === lb) return u;
    }
    var base = rid.replace(/\s*concentration\s*$/i, "").trim();
    for (var j = 0; j < list.length; j++) {
      var t = list[j];
      var ak = (t.analyteKey || t.label || "").toLowerCase();
      if (ak && (base.indexOf(ak) >= 0 || ak.indexOf(base) >= 0)) return t;
    }
    return null;
  }

  function clearAllElemPlots() {
    lastRepPlotAnalytes = [];
    lastRepDetailFields = [];
    repDetailLastError = "";
    repDetailLoading = false;
    elemPanelView = {};
    document.querySelectorAll(".elem-mini-plot").forEach(function (cv) {
      showChartMessage(cv, "—");
    });
    document.querySelectorAll(".elem-plot-hint").forEach(function (el) {
      el.textContent = "";
      el.classList.add("hidden");
    });
    document.querySelectorAll(".elem-plot-stack").forEach(function (el) {
      show(el, true);
    });
    document.querySelectorAll(".elem-detail-stack").forEach(function (el) {
      show(el, false);
    });
    document.querySelectorAll(".elem-viz-toggle").forEach(function (btn) {
      btn.textContent = "详情";
    });
    document.querySelectorAll(".elem-detail-grid").forEach(function (g) {
      g.innerHTML = "";
    });
  }

  function drawAnalytePlotsInPanels() {
    document.querySelectorAll(".elem-plot-hint").forEach(function (el) {
      el.textContent = "";
      el.classList.add("hidden");
    });
    (lastElementStats || []).forEach(function (st) {
      var sid = safeDomId(st.registryId || "");
      var chartEl = $("elem-chart-" + sid);
      var hint = $("elem-plot-hint-" + sid);
      if (!chartEl) return;
      var ser = findPlotSeriesForRegistry(st.registryId || "", st.label || "");
      if (ser && ser.points && ser.points.length >= 2) {
        drawLineOnChart(chartEl, ser.points, ser.bounds || null);
        if (hint) {
          hint.textContent =
            "RepPlot · Tag " +
            String(selectedRepTag || "") +
            " · " +
            (ser.analyteKey || "");
          hint.classList.remove("hidden");
        }
      } else {
        showChartMessage(chartEl, "无曲线或元素不匹配");
        if (hint) hint.classList.add("hidden");
      }
    });
  }

  async function loadRepsInternal() {
    if (!selectedSetKey) return;
    var q =
      "?set_key=" +
      encodeURIComponent(selectedSetKey) +
      "&include_detail=false&tag=-1";
    var data = await fetchJson("/api/instrument/set-reps" + q);
    if (data && data.ok) {
      lastRepAnalyteColumns = data.repAnalyteColumns || [];
      lastElementStats = data.elementStats || [];
      renderRepsThead();
      renderRepsTbody(data.replicates);
      setSetsBanner("", "");
      buildElementPanels();
      showSetLevelViz();
      clearAllElemPlots();
    } else {
      lastRepAnalyteColumns = [];
      lastElementStats = [];
      renderRepsThead();
      renderRepsTbody([]);
      setSetsBanner((data && data.error) || "SetReps 失败", "err");
      buildElementPanels();
    }
  }

  async function onSetSelected(sk) {
    selectedSetKey = sk;
    selectedRepTag = null;
    document.querySelectorAll(".reppick").forEach(function (x) {
      x.checked = false;
    });
    await loadRepsInternal();
  }

  async function onRepSelected(tag) {
    selectedRepTag = tag;
    showSetLevelViz();
    elemPanelView = {};
    document.querySelectorAll(".elem-plot-stack").forEach(function (el) {
      show(el, true);
    });
    document.querySelectorAll(".elem-detail-stack").forEach(function (el) {
      show(el, false);
    });
    document.querySelectorAll(".elem-viz-toggle").forEach(function (btn) {
      btn.textContent = "详情";
    });
    await Promise.all([loadRepPlot(), loadRepDetail()]);
  }

  function clearSetsSampleArea(emptySetsHint) {
    selectedSetKey = "";
    selectedRepTag = null;
    lastRepAnalyteColumns = [];
    lastElementStats = [];
    lastReps = [];
    renderRepsThead();
    renderRepsTbody([]);
    buildElementPanels();
    clearAllElemPlots();
    showSetLevelViz();
    if (emptySetsHint != null) {
      renderSetsTbody([], emptySetsHint);
    }
  }

  function applySetsListPayload(data, emptyHint) {
    lastAnalyteDefs = (data && data.analyteDefs) || [];
    renderSetsThead();
    if (!data || !data.items || !data.items.length) {
      renderSetsTbody([], emptyHint || "当前无 Set 记录。");
    } else {
      renderSetsTbody(data.items);
    }
    updateSetsPaginationButtons(data || {});
    clearSetsSampleArea(null);
  }

  async function loadSets() {
    var fk = ($("sets-filter-key").value || "").trim();
    var n = parseInt($("sets-number").value, 10) || 10;
    var sa = parseInt($("sets-start-at").value, 10);
    if (isNaN(sa)) sa = -1;
    var q =
      "?number=" +
      encodeURIComponent(String(n)) +
      "&start_at=" +
      encodeURIComponent(String(sa)) +
      "&filter_key=" +
      encodeURIComponent(fk);
    var data = await fetchJson("/api/instrument/sets" + q);
    if (data && data.ok) {
      applySetsListPayload(
        data,
        "查询成功：当前无 Set 记录（参数无匹配或仪器列表为空）。"
      );
      setSetsBanner("", "");
    } else {
      renderSetsTbody([], "查询失败，请查看上方提示或网关日志。");
      setSetsBanner((data && data.error) || "Sets 查询失败", "err");
      updateSetsPaginationButtons({});
    }
  }

  async function loadRemoteImportSets() {
    var btn = $("btn-sets-remote-import");
    if (btn) btn.disabled = true;
    setSetsBanner("正在获取最近远程添加的 Set…", "");
    clearSetsSampleArea("正在加载…");
    lastAnalyteDefs = [];
    renderSetsThead();
    try {
      var data = await fetchJson("/api/instrument/remote-import-sets");
      if (data && data.ok) {
        applySetsListPayload(
          data,
          "远程录入成功，但 SetsEx 未返回 Set 行（请确认已执行 AddSamples）。"
        );
        var n = data.items ? data.items.length : 0;
        var k = data.keys ? data.keys.length : 0;
        setSetsBanner(
          n ? "已从 LastRemoteAddedSets + SetsEx 载入 " + n + " 条（Key 数 " + k + "）。" : "",
          ""
        );
      } else {
        clearSetsSampleArea("远程录入失败。");
        setSetsBanner((data && data.error) || "远程录入 Sets 失败", "err");
        updateSetsPaginationButtons({});
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function loadRepPlot() {
    if (!selectedSetKey || selectedRepTag === null) {
      clearAllElemPlots();
      return;
    }
    var q =
      "?set_key=" +
      encodeURIComponent(selectedSetKey) +
      "&tag=" +
      encodeURIComponent(String(selectedRepTag));
    var data = await fetchJson("/api/instrument/rep-plot" + q);
    if (!data || !data.ok) {
      lastRepPlotAnalytes = [];
      document.querySelectorAll(".elem-mini-plot").forEach(function (c) {
        showChartMessage(c, data && data.error ? "RepPlot 失败" : "—");
      });
      return;
    }
    lastRepPlotAnalytes = data.analytePlotSeries || [];
    if (data.hasAnalytePlotSeries && lastRepPlotAnalytes.length) {
      drawAnalytePlotsInPanels();
      return;
    }
    if (data.hasSeries && data.series && data.series.length) {
      var plots = document.querySelectorAll(".elem-mini-plot");
      if (plots[0] && data.series[0].points)
        drawLineOnChart(plots[0], data.series[0].points, null);
      for (var li = 1; li < plots.length; li++) {
        showChartMessage(plots[li], "旧版曲线仅显示于首卡片");
      }
      return;
    }
    if (data.hasImage && data.imageBase64) {
      var mime = data.imageMime || "image/png";
      var plots = document.querySelectorAll(".elem-mini-plot");
      if (plots.length && plots[0]) {
        showChartImage(plots[0], mime, data.imageBase64);
      }
      for (var pi = 1; pi < plots.length; pi++) {
        showChartMessage(plots[pi], "谱图见首元素卡片");
      }
      return;
    }
    document.querySelectorAll(".elem-mini-plot").forEach(function (c) {
      showChartMessage(c, "无曲线数据");
    });
  }

  var analysisViz = $("analysis-viz");
  if (analysisViz) {
    analysisViz.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".elem-viz-toggle");
      if (!btn) return;
      ev.preventDefault();
      var sid = btn.getAttribute("data-sid");
      if (!sid) return;
      var cur = elemPanelView[sid] || "plot";
      setElemPanelView(sid, cur === "plot" ? "detail" : "plot");
    });
  }

  window.addEventListener("resize", function () {
    resizeAllElemCharts();
  });

  $("btn-load-sets").addEventListener("click", loadSets);
  if ($("btn-sets-next")) {
    $("btn-sets-next").addEventListener("click", function () {
      var p = lastSetsPagination;
      if (p && p.prevNewerStartAt != null) {
        $("sets-start-at").value = String(p.prevNewerStartAt);
        loadSets();
      }
    });
  }
  if ($("btn-sets-prev")) {
    $("btn-sets-prev").addEventListener("click", function () {
      var p = lastSetsPagination;
      if (p && p.nextOlderStartAt != null) {
        $("sets-start-at").value = String(p.nextOlderStartAt);
        loadSets();
      }
    });
  }
  if ($("btn-sets-last")) {
    $("btn-sets-last").addEventListener("click", function () {
      $("sets-start-at").value = "-1";
      loadSets();
    });
  }
  if ($("btn-sets-remote-import")) {
    $("btn-sets-remote-import").addEventListener("click", loadRemoteImportSets);
  }

  renderSetsThead();
  renderRepsThead();

  var btnInfo = $("btn-instrument-info");
  if (btnInfo) btnInfo.addEventListener("click", function () { void openInstrumentInfoModal(); });
  var modalClose = $("modal-ii-close");
  if (modalClose) modalClose.addEventListener("click", closeInstrumentInfoModal);
  var modalBackdrop = $("modal-instrument-info");
  if (modalBackdrop) {
    modalBackdrop.addEventListener("click", function (ev) {
      if (ev.target === modalBackdrop) closeInstrumentInfoModal();
    });
  }
  ["btn-footer-tcp", "btn-footer-user", "btn-footer-right"].forEach(function (bid) {
    var bx = $(bid);
    if (bx) bx.addEventListener("click", function () { void openGatewaySettingsModal(); });
  });
  var gwClose = $("modal-gw-close");
  if (gwClose) gwClose.addEventListener("click", closeGatewaySettingsModal);
  var gwCancel = $("modal-gw-cancel");
  if (gwCancel) gwCancel.addEventListener("click", closeGatewaySettingsModal);
  var gwSave = $("modal-gw-save");
  if (gwSave) gwSave.addEventListener("click", function () { void saveGatewaySettings(); });
  var gwBackdrop = $("modal-gateway-settings");
  if (gwBackdrop) {
    gwBackdrop.addEventListener("click", function (ev) {
      if (ev.target === gwBackdrop) closeGatewaySettingsModal();
    });
  }
  var tpRef = $("btn-refresh-transports");
  if (tpRef) tpRef.addEventListener("click", function () { void refreshTransportsPage(); });
  var tpList = $("tp-list");
  if (tpList) {
    tpList.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".btn-tp-expand");
      if (!btn) return;
      var row = btn.closest(".tp-row");
      if (!row) return;
      var key = row.dataset.key || "";
      void toggleTransportDetail(key, row, btn);
    });
  }
  var mdRef = $("btn-refresh-methods");
  if (mdRef) mdRef.addEventListener("click", function () { void refreshMethodsPage(); });
  var mdList = $("md-list");
  if (mdList) {
    mdList.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".btn-tp-expand");
      if (!btn) return;
      var row = btn.closest(".md-row");
      if (!row) return;
      var key = row.dataset.key || "";
      void toggleMethodDetail(key, row, btn);
    });
  }
  var stRef = $("btn-refresh-standards");
  if (stRef) stRef.addEventListener("click", function () { void refreshStandardsPage(); });
  var stList = $("st-list");
  if (stList) {
    stList.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".btn-tp-expand");
      if (!btn) return;
      var row = btn.closest(".st-row");
      if (!row) return;
      var key = row.dataset.key || "";
      void toggleStandardDetail(key, row, btn);
    });
  }
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      closeAllNavMenus();
      closeInstrumentInfoModal();
      closeGatewaySettingsModal();
    }
  });

  loadConfig();
  refreshQueue();
  startStatusPolling();
  if ($("view-analysis") && !$("view-analysis").classList.contains("hidden")) {
    maybeAutoLoadSetsOnceOnAnalysis();
  }
})();
