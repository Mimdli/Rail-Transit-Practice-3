/* 数据与场景：区分线路数据、真实场景控制和开发调试信息。 */
(function exposeSceneCenter(global) {
  const template = `
    <header class="page-head"><div><h1>数据与场景</h1><p>线路数据完整度、仿真环境与故障场景</p></div><button class="button active" id="scenarioApply">应用当前场景</button></header>
    <nav class="interface-tabs" aria-label="数据与场景功能"><button class="active" data-scene-tab="data">线路基础数据</button><button data-scene-tab="scenarios">仿真场景</button><button data-scene-tab="debug">开发调试</button></nav>
    <div class="scene-panels">
      <section class="scene-panel active" data-scene-panel="data"><div class="data-metrics"><div><strong id="sceneStationCount">—</strong><span>运营车站</span></div><div><strong id="sceneLinkCount">—</strong><span>主线 Link</span></div><div><strong id="sceneSignalCount">—</strong><span>信号机</span></div><div><strong id="sceneTrainCount">—</strong><span>在线列车</span></div></div><div class="pane scene-data"><div class="pane-title"><h2>数据详情</h2><span>来自后端实时快照</span></div><div class="table-scroll"><table class="data-table"><thead><tr><th>类型</th><th>编号</th><th>名称/方向</th><th>位置</th><th>状态</th></tr></thead><tbody id="sceneDataBody"></tbody></table></div></div></section>
      <section class="scene-panel" data-scene-panel="scenarios" hidden><div class="scene-page"><div class="pane-title"><h2>运行场景</h2><span id="scenarioState">请选择场景</span></div><div class="scene-grid" id="scenarioGrid"><button class="scene active" data-scenario="normal"><span class="status">可用</span><h3>正常运行</h3><p>清除故障并恢复标准运行条件</p></button><button class="scene" data-scenario="low_adhesion"><span class="status warn">可用</span><h3>雨天低黏着</h3><p>黏着系数降至 0.10，制动距离增加</p></button><button class="scene" data-scenario="low_voltage"><span class="status warn">可用</span><h3>低电压</h3><p>降低全线牵引能力</p></button><button class="scene" data-scenario="power_outage"><span class="status stop">可用</span><h3>供电中断</h3><p>切除全线牵引并记录事件</p></button><button class="scene" data-scenario="occupancy_conflict"><span class="status warn">可用</span><h3>区段占用冲突</h3><p>注入占用并验证联锁拒绝</p></button><button class="scene" data-scenario="communication_outage"><span class="status stop">可用</span><h3>通信中断</h3><p>强制外部接口显示中断</p></button></div></div></section>
      <section class="scene-panel" data-scene-panel="debug" hidden><div class="debug-page"><div class="pane-title"><h2>开发调试</h2><span>非运营功能</span></div><div class="command-feedback">该区域仅用于开发和答辩调试，不属于实际运营命令。</div><dl class="detail-list"><dt>数据源</dt><dd>数据库线路</dd><dt>仿真后端</dt><dd>实时连接</dd><dt>当前场景</dt><dd id="debugScenario">—</dd><dt>数据序列</dt><dd id="debugSequence">—</dd></dl><div class="command-grid"><button class="button" disabled title="后端尚未提供数据刷新命令">刷新数据</button><button class="button danger" disabled title="后端尚未提供线路重载命令">重载线路</button></div></div></section>
    </div>`;

  function bind() { document.querySelectorAll("[data-scene-tab]").forEach(button => button.onclick = () => { document.querySelectorAll("[data-scene-tab]").forEach(item => item.classList.toggle("active", item === button)); document.querySelectorAll("[data-scene-panel]").forEach(panel => { const active = panel.dataset.scenePanel === button.dataset.sceneTab; panel.hidden = !active; panel.classList.toggle("active", active); }); }); }
  function render(state) {
    if (!state) return;
    const directions = state.line.directions || {}, links = [...(directions.down || []), ...(directions.up || [])];
    const set = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
    set("sceneLinkCount", links.length); set("debugScenario", state.activeScenario); set("debugSequence", `#${state.sequence}`);
    const body = document.getElementById("sceneDataBody"); if (!body) return;
    const stationRows = state.stations.map(item => ["车站", item.id, item.name, item.position == null ? "—" : `${item.position.toFixed(1)} m`, "已加载"]), signalRows = state.signals.slice(0, 20).map(item => ["信号机", item.id, item.direction, `Seg ${item.segmentId}`, item.aspect]);
    body.innerHTML = [...stationRows, ...signalRows].map(row => `<tr>${row.map(value => `<td>${value}</td>`).join("")}</tr>`).join("");
  }
  global.SceneCenter = Object.freeze({ template, bind, render });
}(window));
