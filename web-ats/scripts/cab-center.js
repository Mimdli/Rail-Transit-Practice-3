/* 司机台联调：实时输入、显示屏报文与 PLC 开关量输出。 */
(function exposeCabCenter(global) {
  const outputs = [
    ["indicator_hv_contactor", "高压接触器指示"],
    ["indicator_brake_release", "制动缓解指示"],
    ["indicator_door_closed", "车门关闭指示"],
    ["indicator_network_fault", "网络故障指示"],
    ["mode_ato_available", "ATO 可用"],
    ["mode_ato_active", "ATO 激活"],
    ["mode_ar", "自动折返"],
    ["btn_emergency_brake", "紧急制动输出"],
    ["btn_forced_release", "强缓输出"],
    ["btn_forced_pump", "强泵输出"],
    ["btn_emergency_command", "紧急指令输出"],
    ["btn_parking_brake", "停放制动输出"],
    ["btn_open_left", "左门开启输出"],
    ["btn_open_right", "右门开启输出"],
    ["btn_close_left", "左门关闭输出"],
    ["btn_close_right", "右门关闭输出"],
  ];
  const template = `
    <header class="page-head"><div><h1>司机台联调</h1><p>PLC 输入解析、显示屏实时数据与开关量输出</p></div><div><span id="cabNetworkStatus" class="network-state">网络未启动</span><button class="button" data-open-page="interface">返回接口与网络</button> <button class="button" id="cabNetworkToggleBtn">联调连接</button></div></header>
    <div class="page-body split"><section class="pane"><div class="pane-title"><h2>司机台实时输入</h2><span id="plcInputSummary">等待数据</span></div><div class="kv-grid"><div class="kv"><span>方向手柄</span><b id="plcDirection">--</b></div><div class="kv"><span>主手柄</span><b id="plcHandle">--</b></div><div class="kv"><span>牵引级位</span><b id="plcTraction">--</b></div><div class="kv"><span>制动级位</span><b id="plcBrake">--</b></div><div class="kv"><span>ATO 状态</span><b id="plcAto">--</b></div><div class="kv"><span>紧急制动</span><b id="plcEmergency">--</b></div><div class="kv"><span>司机钥匙</span><b id="plcMasterKey">--</b></div><div class="kv"><span>警惕开关</span><b id="plcAlert">--</b></div><div class="kv"><span>左门</span><b id="plcDoorLeft">--</b></div><div class="kv"><span>右门</span><b id="plcDoorRight">--</b></div><div class="kv"><span>门模式</span><b id="plcDoorMode">--</b></div><div class="kv"><span>外部照明</span><b id="plcLight">--</b></div></div><div class="pane-title cab-packet-title"><h2>最近报文</h2><span id="plcRawAge">--</span></div><div class="device-list" id="cabRawPackets"><div class="log-empty">等待 PLC 数据…</div></div></section>
    <aside class="pane"><div class="pane-title"><h2>显示屏自动发送</h2><span id="cabDisplaySummary">等待实时数据</span></div><table class="data-table"><thead><tr><th>屏幕</th><th>端口</th><th>发送 / 接收</th><th>最近</th></tr></thead><tbody id="cabDisplayRows"></tbody></table><div class="pane-title" style="margin-top:24px"><h2>司机台显示数据</h2><span>页面与实际发包共用实时状态</span></div><div id="cabDisplayGrid" style="display:grid;grid-template-columns:1fr 1fr;gap:4px 24px"></div>
    <div class="pane-title cab-output-title"><h2>PLC 开关量输出</h2><span>28 字节周期报文</span></div><div class="plc-output-mode"><label><input type="checkbox" id="plcManualOverride"> 启用手动覆盖</label><span class="status" id="plcOutputModeStatus">自动跟随仿真</span><small id="plcOutputSpeed">车速 0 km/h</small></div><div class="plc-light-grid">${outputs.map(([id, label]) => `<label><input type="checkbox" data-plc-output="${id}" disabled> ${label}</label>`).join("")}</div><button class="button active cab-send" id="plcLightsSend" disabled>发送并保持手动输出</button><div class="command-feedback" id="plcLightsFeedback">自动模式会同步供电、ATO、车门、制动和实时车速。</div>
    <div class="pane-title cab-output-title"><h2>ATP / 原始16位输出</h2><span>发送后自动进入手动保持</span></div><div class="inline-form"><div class="field"><label for="plcOutputValue">输出值</label><input id="plcOutputValue" type="number" min="0" max="65535" value="0"></div><button class="button" id="plcOutputSend">发送</button></div><div class="command-feedback" id="plcOutputFeedback">等待发送</div></aside></div>`;

  function render(state) {
    const outputsState = state?.plc_output_state || {};
    const manual = Boolean(state?.plc_output_manual_override);
    const toggle = document.getElementById("plcManualOverride");
    if (toggle && document.activeElement !== toggle) toggle.checked = manual;
    document.querySelectorAll("[data-plc-output]").forEach(input => {
      if (document.activeElement !== input) input.checked = Boolean(outputsState[input.dataset.plcOutput]);
      input.disabled = !manual;
    });
    const send = document.getElementById("plcLightsSend"); if (send) send.disabled = !manual;
    const status = document.getElementById("plcOutputModeStatus"); if (status) { status.textContent = manual ? "手动覆盖" : "自动跟随仿真"; status.className = `status ${manual ? "warn" : ""}`; }
    const speed = document.getElementById("plcOutputSpeed"); if (speed) speed.textContent = `报文车速 ${Number(state?.plc_output_vehicle_speed || 0)} km/h`;
  }
  global.CabCenter = Object.freeze({ template, render });
}(window));
