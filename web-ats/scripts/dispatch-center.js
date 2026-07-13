/* 调度中心：组织列车管理、真实运营命令和隔离的仿真调试功能。 */
(function exposeDispatchCenter(global) {
  const template = `
    <header class="page-head"><div><h1>调度中心</h1><p>全线列车管理、交路配置与运营命令</p></div><div class="data-health"><i class="dot"></i><span id="dispatchFreshness">等待实时数据</span></div></header>
    <div class="page-body dispatch-layout">
      <section class="pane dispatch-main">
        <div class="pane-title"><h2>全线列车</h2><span id="liveTrainCount">等待实时数据</span></div>
        <details class="dispatch-add"><summary>添加列车</summary>
          <form class="dispatch-form" id="addTrainForm">
            <div class="field"><label for="newTrainId">列车编号</label><input id="newTrainId" value="2车" maxlength="20" required></div>
            <div class="field"><label for="newTrainStation">初始车站</label><select id="newTrainStation"></select></div>
            <div class="field"><label for="newTrainDirection">运行方向</label><select id="newTrainDirection"><option value="1">下行</option><option value="-1">上行</option></select></div>
            <div class="field"><label for="newTrainPlan">交路</label><select id="newTrainPlan"><option value="mainline_loop">主线往返</option></select></div>
            <div class="field"><label for="newTrainMode">驾驶模式</label><select id="newTrainMode" disabled title="后端当前按 ATO 模式创建列车"><option>ATO 自动驾驶</option></select></div>
            <div class="field"><label for="newTrainLink">初始 Link / Seg</label><input id="newTrainLink" value="由始发站自动计算" disabled></div>
            <label class="check-field"><input type="checkbox" disabled> 绑定实体司机台 <span>待后端接口</span></label>
            <button class="button active" type="submit">确认加车</button>
          </form>
        </details>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>列车</th><th>状态</th><th>方向</th><th>速度</th><th>下一站</th><th>交路</th></tr></thead><tbody id="dispatchTrainRows"></tbody></table></div>
        <div class="pane-title dispatch-chart-title"><h2>运行图</h2><span>列车在线位置 · 实时快照</span></div>
        <div class="dispatch-diagram" id="dispatchDiagram"><div class="log-empty">等待列车位置数据…</div></div>
      </section>
      <aside class="pane dispatch-command">
        <div class="pane-title"><h2>当前选中列车</h2><span>运营命令</span></div>
        <div class="command-hero"><strong id="dispatchSelected">1车</strong><span><i class="dot"></i><span id="dispatchState">等待状态</span></span></div>
        <div class="kv"><span>当前位置</span><b id="dispatchPosition">—</b></div><div class="kv"><span>当前交路</span><b id="dispatchPlan">—</b></div><div class="kv"><span>驾驶模式</span><b>ATO</b></div>
        <div class="command-grid"><button class="button" data-command="depart">发车</button><button class="button" data-command="hold">扣车</button><button class="button" data-command="release">解除扣车</button><button class="button" data-command="restore">恢复运行</button><button class="button" data-command="plan">设置主线交路</button><button class="button danger" data-command="emergency-stop">紧急停车</button><button class="button danger wide" id="removeTrainBtn">删除当前列车</button></div>
        <div class="command-feedback" id="commandFeedback" role="status" aria-live="polite">尚未下发调度命令</div>
        <details class="debug-tools"><summary>仿真调试操作</summary><p>该区域为仿真调试功能，不属于实际运营命令。</p><div class="field"><label for="debugStation">跳转至车站</label><select id="debugStation"></select></div><div class="command-grid"><button class="button" data-train-control="jump" data-value-input="debugStation">执行跳转</button><button class="button" data-train-control="fast-forward">快速前进到下一站</button><button class="button" data-train-control="level" data-value="COAST">重置为惰行</button></div></details>
      </aside>
    </div>`;

  function bind(state) {
    const debugStation = document.getElementById("debugStation");
    if (debugStation && state) debugStation.innerHTML = state.stations.map(station => `<option value="${station.id}">${station.name}</option>`).join("");
  }

  function render(state) {
    if (!state) return;
    const freshness = document.getElementById("dispatchFreshness");
    if (freshness) freshness.textContent = `快照 #${state.sequence} · 100 ms`;
    const diagram = document.getElementById("dispatchDiagram");
    if (!diagram) return;
    const trains = state.trains.filter(train => train.linkPosition != null);
    if (!trains.length) { diagram.innerHTML = '<div class="log-empty">当前无在线列车</div>'; return; }
    const positions = trains.map(train => train.linkPosition), min = Math.min(...positions), max = Math.max(...positions, min + 1);
    diagram.innerHTML = trains.map(train => {
      const left = 4 + (train.linkPosition - min) / (max - min) * 90;
      return `<div class="diagram-row"><span>${train.id}</span><div class="diagram-track"><i style="left:${left}%" title="${train.id} · ${train.speedKmh.toFixed(1)} km/h"></i></div><b>${train.speedKmh.toFixed(1)} km/h</b></div>`;
    }).join("");
  }

  global.DispatchCenter = Object.freeze({ template, bind, render });
}(window));
