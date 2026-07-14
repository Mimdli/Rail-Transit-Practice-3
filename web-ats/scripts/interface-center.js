/* 接口中心：以真实端点为最小单位展示状态、报文和只读配置。 */
(function exposeInterfaceCenter(global) {
  const template = `
    <header class="page-head"><div><h1>接口与网络</h1><p>多系统连接、通信质量、报文与协议配置</p></div><div><span id="networkToggleStatus" class="network-state">网络未启动</span><button class="button" id="networkToggleBtn">联调连接</button> <button class="button" data-open-page="cab">司机台联调</button> <button class="button" id="exportLogs">导出日志</button></div></header>
    <nav class="interface-tabs" aria-label="接口与网络功能"><button class="active" data-interface-tab="topology">连接拓扑</button><button data-interface-tab="status">实时状态</button><button data-interface-tab="messages">报文监视</button><button data-interface-tab="config">协议配置</button></nav>
    <div class="interface-panels">
      <section class="interface-panel active" data-interface-panel="topology"><div class="topology topology-wide"><div class="node" id="topologySignal">信号系统<br><small>等待状态</small></div><div class="connector"></div><div class="node core">仿真核心<br><small>WebSocket · 100 ms</small></div><div class="connector"></div><div class="node" id="topologyPlc">实体司机台<br><small>等待状态</small></div><div class="topology-branch"><span>车辆状态 UDP</span><span>视景系统 UDP</span><span>司机台显示 TCP</span></div></div><div class="topology-legend"><span class="status">在线</span><span class="status warn">模拟/等待</span><span class="status off">离线</span><span class="status stop">故障</span></div></section>
      <section class="interface-panel" data-interface-panel="status" hidden><div class="pane"><div class="pane-title"><h2>端点通信状态</h2><span id="interfaceSummary">等待实时数据</span></div><div class="table-scroll"><table class="data-table endpoint-table"><thead><tr><th>端点</th><th>协议</th><th>本机</th><th>对端</th><th>方向 / 帧长</th><th>状态</th><th>发送</th><th>接收</th><th>最近通信</th></tr></thead><tbody id="interfaceRows"></tbody></table></div><div class="command-feedback" id="interfaceDetail">统计值来自后端网络模块。</div></div></section>
      <section class="interface-panel" data-interface-panel="messages" hidden><div class="message-toolbar"><div class="field"><label for="messageSystem">通信端点</label><select id="messageSystem"><option value="plc_8001">PLC 主端口 8001</option><option value="plc_8002">PLC 备用 8002</option><option value="plc_8003">PLC 备用 8003</option><option value="signal_gateway">信号系统 :10000</option><option value="vehicle_udp">车辆 UDP :23001</option><option value="vision">视景系统 :8303</option><option value="cab_network">网络屏 :8888</option><option value="cab_signal">信号屏 :9999</option></select></div><div class="field"><label for="messageDirection">方向</label><select id="messageDirection"><option value="recv">接收</option><option value="sent">发送</option></select></div><button class="button" id="messagePause">暂停滚动</button><span id="messageAge">尚无报文</span></div><div class="message-layout"><section><div class="pane-title"><h2>原始 HEX</h2><span id="messageLength">0 Byte</span></div><pre class="packet-hex" id="messageHex">等待真实通信报文…</pre></section><aside><div class="pane-title"><h2>字段解析</h2><span id="messageCheck">等待数据</span></div><dl class="detail-list" id="messageFields"><dt>报文类型</dt><dd>—</dd><dt>通信方向</dt><dd>—</dd><dt>数据来源</dt><dd>后端实时统计</dd></dl></aside></div></section>
      <section class="interface-panel" data-interface-panel="config" hidden><div class="config-layout"><section><div class="pane-title"><h2>生效中的协议配置</h2><span>只读 · 来自后端运行配置</span></div><div class="table-scroll"><table class="data-table endpoint-table"><thead><tr><th>端点</th><th>协议</th><th>本机地址</th><th>对端地址</th><th>方向</th><th>报文长度</th></tr></thead><tbody id="configEndpointRows"><tr><td colspan="6">等待配置数据…</td></tr></tbody></table></div><div class="command-feedback">本页显示当前进程实际采用的地址和端口；修改仍由 config/network_config.py 统一管理。</div></section><aside><button class="button" id="configTestBtn">测试连接</button><p class="config-note">测试会启动全部联调接口，端点状态会在“实时状态”页更新。</p></aside></div></section>
    </div>`;

  let paused = false;
  function bind(toggleNetwork) {
    document.querySelectorAll("[data-interface-tab]").forEach(button => button.onclick = () => {
      document.querySelectorAll("[data-interface-tab]").forEach(item => item.classList.toggle("active", item === button));
      document.querySelectorAll("[data-interface-panel]").forEach(panel => { const active = panel.dataset.interfacePanel === button.dataset.interfaceTab; panel.hidden = !active; panel.classList.toggle("active", active); });
    });
    const pause = document.getElementById("messagePause"); if (pause) pause.onclick = () => { paused = !paused; pause.textContent = paused ? "继续滚动" : "暂停滚动"; };
    const system = document.getElementById("messageSystem"), direction = document.getElementById("messageDirection");
    if (system) system.onchange = () => render(global.__atsLiveState); if (direction) direction.onchange = () => render(global.__atsLiveState);
    const test = document.getElementById("configTestBtn"); if (test) test.onclick = toggleNetwork;
  }

  function setText(id, value) { const node = document.getElementById(id); if (node) node.textContent = value; }
  function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])); }
  function endpoints(state) {
    return Object.values(state?.network_stats || {}).flatMap(stat => stat.endpoints || []);
  }
  function renderConfig(state) {
    const rows = document.getElementById("configEndpointRows"); if (!rows) return;
    rows.innerHTML = endpoints(state).map(endpoint => `<tr><td>${escapeHtml(endpoint.name)}</td><td>${escapeHtml(endpoint.protocol)}</td><td><code>${escapeHtml(endpoint.local)}</code></td><td><code>${escapeHtml(endpoint.remote)}</code></td><td>${escapeHtml(endpoint.direction)}</td><td>${escapeHtml(endpoint.frame_size)}</td></tr>`).join("") || '<tr><td colspan="6">等待配置数据…</td></tr>';
  }
  function render(state) {
    if (!state) return;
    global.__atsLiveState = state;
    renderConfig(state);
    if (paused || !document.getElementById("messageHex")) return;
    const endpointId = document.getElementById("messageSystem")?.value || "plc_8001";
    const direction = document.getElementById("messageDirection")?.value || "recv";
    const endpoint = endpoints(state).find(item => item.id === endpointId) || {};
    const hex = endpoint[direction === "recv" ? "last_recv_packet_hex" : "last_sent_packet_hex"] || "";
    const bytes = hex ? hex.trim().split(/\s+/).length : 0;
    const age = endpoint[direction === "recv" ? "last_recv_ago" : "last_send_ago"];
    setText("messageHex", hex || "等待真实通信报文…"); setText("messageLength", `${bytes} Byte`); setText("messageAge", age == null ? "尚无报文" : `${age} ms 前`); setText("messageCheck", hex ? "已有报文" : "等待数据");
    const plc = state.plc_data || {};
    const fields = [["报文类型", endpoint.name || "通信端点"], ["通信方向", direction === "recv" ? "外部系统 → 仿真核心" : "仿真核心 → 外部系统"], ["本机 / 对端", `${endpoint.local || "—"} / ${endpoint.remote || "—"}`], ["长度", `${bytes} Byte`], ["预期帧长", endpoint.frame_size || "—"]];
    if (endpointId.startsWith("plc_") && Object.keys(plc).length) fields.push(["方向手柄", plc.dir_forward ? "向前" : plc.dir_backward ? "向后" : "零位"], ["牵引级位", plc.traction_level ?? "—"], ["制动级位", plc.brake_level ?? "—"], ["紧急制动", plc.btn_emergency_brake ? "触发" : "正常"]);
    const list = document.getElementById("messageFields"); if (list) list.innerHTML = fields.map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
  }

  global.InterfaceCenter = Object.freeze({ template, bind, render });
}(window));
