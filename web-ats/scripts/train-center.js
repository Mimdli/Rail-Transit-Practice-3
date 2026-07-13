/* 列车中心：集中组织单车概览、驾驶、力学、能耗与编组页面。 */
(function exposeTrainCenter(global) {
  const tabs = [
    ["summary", "运行概览"], ["drive", "驾驶控制"],
    ["dynamics", "力学分析"], ["energy", "能耗分析"],
    ["consist", "编组与参数"],
  ];

  const template = `
    <header class="page-head train-head">
      <div><h1>列车中心</h1><p id="monitorSubtitle">选择列车查看独立运行状态</p></div>
      <div class="field"><label for="monitorTrainSelect">当前列车</label><select id="monitorTrainSelect"></select></div>
    </header>
    <nav class="train-tabs" aria-label="列车中心功能">
      ${tabs.map(([id, label], index) => `<button class="${index ? "" : "active"}" data-train-tab="${id}" aria-selected="${!index}">${label}</button>`).join("")}
    </nav>
    <div class="train-panels">
      <section class="train-panel active" data-train-panel="summary">
        <div class="page-body split"><section class="pane">
          <div class="big-speed"><div><strong id="monitorSpeed">0.0</strong> <small>km/h</small><div class="speed-limit-copy">允许速度 <span id="monitorLimit">--</span> km/h</div></div><div class="arc" id="monitorSpeedArc"></div></div>
          <div class="pane-title train-section-title"><h2>当前运行位置</h2><span id="monitorPosition">--</span></div>
          <table class="data-table"><tbody><tr><td>运行状态</td><td id="monitorStatus">--</td></tr><tr><td>当前交路</td><td id="monitorPlan">--</td></tr><tr><td>目标车站</td><td id="monitorTarget">--</td></tr><tr><td>距目标站</td><td id="monitorDistance">--</td></tr></tbody></table>
        </section><aside class="pane"><div class="pane-title"><h2>车辆实时状态</h2><span>100 ms</span></div><div class="kv-grid"><div class="kv"><span>加速度</span><b id="monitorAcceleration">--</b></div><div class="kv"><span>牵引级位</span><b id="monitorThrottle">--</b></div><div class="kv"><span>制动级位</span><b id="monitorBrake">--</b></div><div class="kv"><span>牵引力</span><b id="monitorTractiveForce">--</b></div><div class="kv"><span>总制动力</span><b id="monitorBrakeForce">--</b></div><div class="kv"><span>累计净能耗</span><b id="monitorEnergy">--</b></div><div class="kv"><span>供电</span><b id="monitorPower">--</b></div><div class="kv"><span>车门</span><b id="monitorDoors">--</b></div></div><div class="command-grid"><button class="button" data-command="depart">发车</button><button class="button" data-command="hold">扣车</button><button class="button" data-command="release">解除扣车</button><button class="button danger" data-command="emergency-stop">紧急停车</button></div></aside></div>
      </section>
      <section class="train-panel" data-train-panel="drive" hidden>
        <div class="page-body split"><section class="pane"><div class="pane-title"><h2>主控手柄</h2><span>手动驾驶实时控制</span></div><div class="handle-readout"><span>当前牵引</span><strong id="driveThrottle">0%</strong><span>当前制动</span><strong id="driveBrake">0%</strong></div><div class="control-scale"><button class="button" data-train-control="level" data-value="P1">P1</button><button class="button" data-train-control="level" data-value="P2">P2</button><button class="button" data-train-control="level" data-value="P3">P3</button><button class="button" data-train-control="level" data-value="COAST">惰行</button><button class="button" data-train-control="level" data-value="B1">B1</button><button class="button" data-train-control="level" data-value="B2">B2</button><button class="button danger" data-train-control="level" data-value="EB">紧急</button></div><div class="command-feedback" id="driveFeedback">请先切换手动模式，再选择司控器级位。</div></section><aside class="pane"><div class="pane-title"><h2>驾驶与安全</h2><span id="driveMode">--</span></div><div class="command-grid mode-controls"><button class="button" data-train-control="mode" data-value="manual">手动驾驶</button><button class="button" data-train-control="mode" data-value="automatic">ATO 自动</button></div><div class="kv"><span>车门状态</span><b id="driveDoors">--</b></div><div class="command-grid door-controls"><button class="button" data-train-control="door" data-value="left">开左门</button><button class="button" data-train-control="door" data-value="right">开右门</button><button class="button wide" data-train-control="door" data-value="close">关闭车门</button></div><div class="kv"><span>线路限速</span><b id="driveLimit">--</b></div><div class="kv"><span>供电状态</span><b id="drivePower">--</b></div></aside></div>
      </section>
      <section class="train-panel" data-train-panel="dynamics" hidden>
        <div class="analysis-layout"><section><div class="pane-title"><h2>纵向力学状态</h2><span>实时快照 · 浏览器保留 120 点</span></div><div class="force-balance"><div><span>牵引力</span><b id="dynTraction">--</b><i class="force-bar traction" id="dynTractionBar"></i></div><div><span>制动力</span><b id="dynBrake">--</b><i class="force-bar brake" id="dynBrakeBar"></i></div></div><div class="chart analysis-chart"><svg id="dynamicsChart" viewBox="0 0 800 260" preserveAspectRatio="none"></svg></div><div class="table-scroll force-table"><table class="data-table"><thead><tr><th>车厢</th><th>速度</th><th>牵引</th><th>制动</th><th>电制动</th><th>空气制动</th><th>Davis</th><th>坡道</th><th>前车钩</th><th>后车钩</th><th>合力</th><th>黏着</th></tr></thead><tbody id="carForceRows"></tbody></table></div></section><aside><div class="kv"><span>速度</span><b id="dynSpeed">--</b></div><div class="kv"><span>加速度</span><b id="dynAcceleration">--</b></div><div class="kv"><span>线路坡度</span><b id="dynGradient">--</b></div><div class="kv"><span>最大车钩力</span><b id="dynCoupler">--</b></div><div class="kv"><span>线路限速</span><b id="dynLimit">--</b></div></aside></div>
      </section>
      <section class="train-panel" data-train-panel="energy" hidden>
        <div class="analysis-layout"><section><div class="pane-title"><h2>能耗累计</h2><span>实时快照 · 浏览器保留 120 点</span></div><div class="energy-total"><strong id="energyTotal">--</strong><span>kWh · 累计净能耗</span></div><div class="chart analysis-chart"><svg id="energyChart" viewBox="0 0 800 260" preserveAspectRatio="none"></svg></div></section><aside><div class="kv"><span>当前牵引级位</span><b id="energyThrottle">--</b></div><div class="kv"><span>牵引电耗</span><b id="energyTraction">--</b></div><div class="kv"><span>再生回馈</span><b id="energyRegen">--</b></div><div class="kv"><span>摩擦热损</span><b id="energyFriction">--</b></div><div class="kv"><span>辅助能耗</span><b id="energyAux">--</b></div><div class="kv"><span>再生率</span><b id="energyRatio">--</b></div></aside></div>
      </section>
      <section class="train-panel" data-train-panel="consist" hidden>
        <div class="analysis-layout"><section><div class="pane-title"><h2>当前编组</h2><span id="consistSummary">—</span></div><div class="consist" id="consistCars"></div><div class="pane-title"><h2>编组预设</h2><span>停车状态下可更换</span></div><div class="command-grid consist-presets"><button class="button" data-train-control="consist" data-value="4M2T">4M2T</button><button class="button" data-train-control="consist" data-value="6M0T">6M0T</button><button class="button" data-train-control="consist" data-value="1M4T">1M4T</button></div><div class="command-feedback" id="consistFeedback">编组变更会真实重建车辆模型。</div></section><aside><div class="pane-title"><h2>运行参数</h2><span>实时生效</span></div><div class="field"><label for="loadLevelSelect">载荷等级</label><select id="loadLevelSelect"><option>AW0</option><option>AW1</option><option>AW2</option><option>AW3</option></select></div><button class="button settings-action" data-train-control="load" data-value-input="loadLevelSelect">应用载荷</button><div class="field settings-field"><label for="dwellTimeInput">停站时间（5–120 s）</label><input id="dwellTimeInput" type="number" min="5" max="120" value="20"></div><button class="button settings-action" data-train-control="dwell" data-value-input="dwellTimeInput">应用停站时间</button><div class="kv"><span>车辆数量</span><b id="consistCarCount">—</b></div><div class="kv"><span>动力配置</span><b id="consistMotorCount">—</b></div></aside></div>
      </section>
    </div>`;

  function bind() {
    document.querySelectorAll("[data-train-tab]").forEach(button => {
      button.onclick = () => {
        document.querySelectorAll("[data-train-tab]").forEach(item => {
          const active = item === button;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", String(active));
        });
        document.querySelectorAll("[data-train-panel]").forEach(panel => {
          const active = panel.dataset.trainPanel === button.dataset.trainTab;
          panel.hidden = !active;
          panel.classList.toggle("active", active);
        });
      };
    });
  }

  const history = new Map();
  function setText(id, value) { const node = document.getElementById(id); if (node) node.textContent = value; }
  function drawChart(id, series) { const svg=document.getElementById(id); if(!svg||!series.length)return; const keys=Object.keys(series[0]).filter(key=>key!=="sequence"), colors=["#39d0d8","#ff5b62","#e9b44c","#5bd28a"], values=series.flatMap(item=>keys.map(key=>Math.abs(item[key]||0))), max=Math.max(1,...values); svg.innerHTML=keys.map((key,keyIndex)=>`<polyline points="${series.map((item,index)=>`${index/Math.max(1,series.length-1)*800},${245-(Math.abs(item[key]||0)/max)*220}`).join(" ")}" fill="none" stroke="${colors[keyIndex%colors.length]}" stroke-width="2" vector-effect="non-scaling-stroke"><title>${key}</title></polyline>`).join(""); }
  function render(train, state) {
    if (!train || !state) return;
    const doors = train.doors.left ? "左门开启" : train.doors.right ? "右门开启" : "关闭锁闭";
    setText("driveThrottle", `${Math.round(train.throttle * 100)}%`); setText("driveBrake", `${Math.round(train.brakeLevel * 100)}%`);
    setText("driveDoors", doors); setText("driveMode", train.runningMode === "MANUAL" ? "手动驾驶" : "ATO 自动"); setText("driveLimit", `${train.speedLimitKmh.toFixed(0)} km/h`); setText("drivePower", state.power.label);
    setText("dynTraction", `${train.tractiveForceKn.toFixed(1)} kN`); setText("dynBrake", `${train.brakeForceKn.toFixed(1)} kN`);
    setText("dynSpeed", `${train.speedKmh.toFixed(1)} km/h`); setText("dynAcceleration", `${train.acceleration.toFixed(2)} m/s²`);
    setText("dynGradient", `${train.gradientPermille.toFixed(2)} ‰`); setText("dynCoupler", `${train.maxCouplerForceKn.toFixed(1)} kN`); setText("dynLimit", `${train.speedLimitKmh.toFixed(0)} km/h`);
    const tractionBar = document.getElementById("dynTractionBar"), brakeBar = document.getElementById("dynBrakeBar");
    if (tractionBar) tractionBar.style.width = `${Math.min(100, Math.abs(train.tractiveForceKn) / 2.2)}%`;
    if (brakeBar) brakeBar.style.width = `${Math.min(100, Math.abs(train.brakeForceKn) / 2.2)}%`;
    const energy=train.energy||{}; setText("energyTotal", train.energyKwh.toFixed(3)); setText("energyThrottle", `${Math.round(train.throttle * 100)}%`); setText("energyTraction", `${(energy.tractionKwh||0).toFixed(3)} kWh`); setText("energyRegen", `${(energy.regenKwh||0).toFixed(3)} kWh`); setText("energyFriction", `${(energy.frictionLossKwh||0).toFixed(3)} kWh`); setText("energyAux", `${(energy.auxKwh||0).toFixed(3)} kWh`); setText("energyRatio", `${((energy.regenRatio||0)*100).toFixed(1)}%`);
    const rows=document.getElementById("carForceRows"); if(rows)rows.innerHTML=(train.carForces||[]).map(car=>`<tr><td>${car.carIndex}</td><td>${car.speedKmh}</td><td>${car.tractiveForceKn}</td><td>${car.brakeForceKn}</td><td>${car.electricBrakeKn}</td><td>${car.frictionBrakeKn}</td><td>${car.davisResistanceKn}</td><td>${car.gradeResistanceKn}</td><td>${car.frontCouplerKn}</td><td>${car.rearCouplerKn}</td><td>${car.netForceKn}</td><td>${car.adhesion==="ok"?"正常":car.adhesion}</td></tr>`).join("");
    const samples=history.get(train.id)||[]; if(!samples.length||samples.at(-1).sequence!==state.sequence){samples.push({sequence:state.sequence,speed:train.speedKmh,traction:train.tractiveForceKn,brake:train.brakeForceKn,energy:energy.netKwh||0,regen:energy.regenKwh||0,aux:energy.auxKwh||0});if(samples.length>120)samples.shift();history.set(train.id,samples);} drawChart("dynamicsChart",samples.map(item=>({sequence:item.sequence,speed:item.speed,traction:item.traction,brake:item.brake})));drawChart("energyChart",samples.map(item=>({sequence:item.sequence,energy:item.energy,regen:item.regen,aux:item.aux})));
    const consist = train.consist || { cars: [], carCount: 0, motorCount: 0 };
    setText("consistSummary", `${consist.carCount} 辆 · ${consist.motorCount}M${consist.carCount-consist.motorCount}T`);
    setText("consistCarCount", `${consist.carCount} 辆`); setText("consistMotorCount", `${consist.motorCount}M${consist.carCount-consist.motorCount}T`);
    const cars = document.getElementById("consistCars"); if (cars) cars.innerHTML = consist.cars.map((car,index) => `<div class="car"><strong>${car.isMotor ? `M${index+1}` : `T${index+1}`}</strong>${car.name}<small>${(car.mass/1000).toFixed(1)} t</small></div>`).join("");
    const load = document.getElementById("loadLevelSelect"), dwell = document.getElementById("dwellTimeInput"); if (load) load.value = train.loadLevel; if (dwell && document.activeElement !== dwell) dwell.value = train.dwellTime;
  }

  global.TrainCenter = Object.freeze({ template, bind, render });
}(window));
