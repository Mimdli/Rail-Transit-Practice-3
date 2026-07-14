/* 信号中心：管理联锁图图层和设备详情交互。 */
(function exposeSignalCenter(global) {
  const layers = { occupancy: true, signals: true, switches: true, trains: true, links: false };
  let selectedSignalKey = "";
  const template = `
    <header class="page-head"><div><h1>信号与联锁</h1><p>线路拓扑、区段占用、信号显示与进路锁闭</p></div><span class="status" id="signalRealtimeState">等待数据</span></header>
    <div class="signal-layers" aria-label="联锁图图层">
      <span>基础图层</span>
      <label><input type="checkbox" data-signal-layer="occupancy" checked> 占用/锁闭</label><label><input type="checkbox" data-signal-layer="signals" checked> 信号机</label><label><input type="checkbox" data-signal-layer="switches" checked> 道岔支线</label><label><input type="checkbox" data-signal-layer="trains" checked> 列车</label>
      <span>高级图层</span><label><input type="checkbox" data-signal-layer="links"> Link</label><label class="disabled-layer"><input type="checkbox" disabled> Seg</label><label class="disabled-layer"><input type="checkbox" disabled> 限速</label><label class="disabled-layer"><input type="checkbox" disabled> 坡度</label>
    </div>
    <div class="signal-workspace">
      <section class="pane"><div class="pane-title"><h2>全线联锁图</h2><span id="signalSummary">正在同步线路拓扑</span></div><div class="signal-canvas"><svg id="signalTrackSvg" viewBox="0 0 1100 430" role="img" aria-label="后端主干线实时联锁图"></svg></div><p class="canvas-help">滚轮缩放 · 拖动平移 · 双击复位 · 点击信号机或区段查看详情</p></section>
      <aside class="signal-side"><section class="signal-detail" id="signalDetail"><div class="pane-title"><h2>设备详情</h2><span>点击图中设备</span></div><div class="detail-empty">选择信号机或 Link 区段后显示防护、占用和锁闭信息。</div></section><section class="signal-events"><div class="pane-title"><h2>实时限制状态</h2><span id="signalDeviceCount">0 项</span></div><div class="device-list" id="signalDeviceList"><div class="log-empty">等待后端快照…</div></div></section></aside>
    </div>`;

  function bind(onLayerChange) {
    document.querySelectorAll("[data-signal-layer]").forEach(input => {
      input.checked = layers[input.dataset.signalLayer];
      input.onchange = () => { layers[input.dataset.signalLayer] = input.checked; onLayerChange(); };
    });
  }

  function bindCanvas(state) {
    applyLayers();
    document.querySelectorAll("#signalTrackSvg [data-signal-id]").forEach(node => {
      const activate = () => showSignal(state.signals.find(signal => signal.key === node.dataset.signalKey));
      node.onclick = activate;
      node.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } };
    });
    document.querySelectorAll("#signalTrackSvg [data-switch-id]").forEach(node => {
      const activate = () => showSwitch(state, node.dataset.switchId);
      node.onclick = activate;
      node.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } };
    });
    document.querySelectorAll("#signalTrackSvg [data-switch-component]").forEach(node => {
      const activate = () => showSwitchRoute(state, node.dataset.switchComponent, node.dataset.routeLabel);
      node.onclick = activate;
      node.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } };
    });
    document.querySelectorAll("#signalTrackSvg [data-signal-link]").forEach(node => {
      const activate = () => showLink(state, node.dataset.direction, node.dataset.signalLink);
      node.onclick = activate;
      node.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } };
    });
    applySelection();
  }

  function applyLayers() {
    document.querySelectorAll(".signal-section").forEach(node => {
      node.setAttribute("stroke", layers.occupancy ? node.dataset.stateStroke : "#4a5c65");
      node.setAttribute("stroke-width", layers.occupancy ? node.dataset.stateWidth : "4");
    });
    document.querySelectorAll(".signal-device").forEach(node => node.style.display = layers.signals ? "" : "none");
    document.querySelectorAll(".signal-switch").forEach(node => node.style.display = layers.switches ? "" : "none");
    document.querySelectorAll(".signal-train").forEach(node => node.style.display = layers.trains ? "" : "none");
    document.querySelectorAll(".signal-link-label").forEach(node => node.style.display = layers.links ? "" : "none");
  }

  function applySelection() {
    document.querySelectorAll(".signal-device").forEach(node => {
      node.classList.toggle("selected", node.dataset.signalKey === selectedSignalKey);
    });
  }

  function detailRows(title, status, rows) {
    const detail = document.getElementById("signalDetail"); if (!detail) return;
    detail.innerHTML = `<div class="pane-title"><h2>${title}</h2><span class="status ${status.className}">${status.label}</span></div><dl class="detail-list">${rows.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("")}</dl>`;
  }

  function showSignal(signal) {
    if (!signal) return;
    selectedSignalKey = signal.key;
    applySelection();
    const aspect = signal.aspect === "RED" ? ["stop", "红灯"] : signal.aspect === "YELLOW" ? ["warn", "黄灯"] : ["", "绿灯"];
    detailRows(signal.id, { className: aspect[0], label: aspect[1] }, [["当前显示", aspect[1]], ["防护方向", signal.direction], ["所属 Seg", signal.segmentId], ["区段偏移", `${signal.offset.toFixed(1)} m`], ["线路位置", signal.linkPosition == null ? "未映射" : `${signal.linkPosition.toFixed(1)} m`], ["允许开放", signal.aspect === "GREEN" ? "是" : "否"], ["限制原因", signal.aspect === "RED" ? "前方区段占用或进路未建立" : signal.aspect === "YELLOW" ? "前方信号限制" : "无"]]);
  }

  function showSwitch(state, switchId) {
    const points = (state.line.switchComponents || []).flatMap(component =>
      component.points.map(point => ({ ...point, role: component.role })));
    const point = points.find(item => String(item.switchId) === String(switchId));
    if (!point) return;
    const roleLabels = { crossover: "交叉渡线", "single-crossover": "单渡线", turnback: "折返线", stub: "尽头线", siding: "侧线" };
    detailRows(`道岔 ${point.name}`, { className: "", label: "定位" }, [["线路用途", roleLabels[point.role] || "联锁支线"], ["所在方向", point.direction === "down" ? "下行" : "上行"], ["汇合 Seg", point.mergeSegmentId], ["定位 Seg", point.normalSegmentId], ["反位 Seg", point.reverseSegmentId], ["图上位置", `${point.position.toFixed(1)} m`]]);
  }

  function showSwitchRoute(state, componentIds, label) {
    const ids = componentIds.split(",").map(Number);
    const components = (state.line.switchComponents || []).filter(component => ids.includes(component.id));
    const points = components.flatMap(component => component.points);
    if (!points.length) return;
    const roles = { crossover: "交叉渡线", "single-crossover": "单渡线", turnback: "折返线", stub: "尽头线", siding: "侧线" };
    const segmentIds = [...new Set(points.flatMap(point => [point.mergeSegmentId, point.normalSegmentId, point.reverseSegmentId]))];
    detailRows(label, { className: "", label: "拓扑数据" }, [["线路类型", [...new Set(components.map(component => roles[component.role] || "联锁支线"))].join(" / ")], ["包含道岔", points.map(point => point.name).join("、")], ["道岔数量", `${points.length} 个`], ["关联 Seg", segmentIds.join("、")], ["数据来源", "ATS 线路布局"], ["图形位置", "均衡示意坐标"]]);
  }

  function showLink(state, direction, id) {
    const link = (state.line.directions[direction] || []).find(item => String(item.link_id) === String(id)); if (!link) return;
    const owners = state.occupancy[id] || [], lock = state.locks[id];
    detailRows(`Link ${id}`, { className: owners.length ? "stop" : lock ? "warn" : "", label: owners.length ? "占用" : lock ? "锁闭" : "空闲" }, [["运行方向", direction === "down" ? "下行" : "上行"], ["里程范围", `${link.start_m.toFixed(1)}–${link.end_m.toFixed(1)} m`], ["区段长度", `${link.length_m.toFixed(1)} m`], ["占用列车", owners.join("、") || "无"], ["进路锁闭", lock || "未锁闭"], ["坡度", "未采集"], ["限速", "未采集"]]);
  }

  global.SignalCenter = Object.freeze({ template, layers, bind, bindCanvas });
}(window));
