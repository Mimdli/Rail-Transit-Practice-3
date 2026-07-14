/* 折返演示地图：用独立的前端状态机讲解进路、道岔、换端和返程。 */
(function exposeDemoMap(global) {
  const template = `
    <header class="page-head demo-map-head">
      <div><h1>折返与道岔演示</h1><p>概念动画 · 展示进路锁闭、终点换端、换股道与返程</p></div>
      <span class="status warn">教学演示</span>
    </header>
    <div class="demo-map-workspace">
      <section class="demo-map-main">
        <div class="demo-map-toolbar">
          <div class="demo-map-controls" aria-label="演示控制">
            <button class="button active" id="demoMapPlay" type="button">开始演示</button>
            <button class="button" id="demoMapStep" type="button">单步</button>
            <button class="button" id="demoMapReset" type="button">重置</button>
          </div>
          <div class="demo-map-time"><span id="demoMapClock">00:00.0</span><small>演示时间</small></div>
        </div>
        <div class="demo-map-canvas">
          <svg id="demoMapSvg" viewBox="0 0 960 430" role="img" aria-labelledby="demoMapTitle demoMapDesc">
            <title id="demoMapTitle">双线终点折返演示地图</title>
            <desc id="demoMapDesc">列车从站A沿下行线经过站B和站C到达站D，通过D端折返道岔换到上行线并返回站A。</desc>

            <text class="demo-direction" x="48" y="126">下行 →</text>
            <text class="demo-direction" x="48" y="306">← 上行</text>

            <g class="demo-route-base">
              <line id="demoDownAB" x1="120" y1="160" x2="340" y2="160" />
              <line id="demoDownBC" x1="340" y1="160" x2="570" y2="160" />
              <line id="demoDownCD" x1="570" y1="160" x2="840" y2="160" />
              <line id="demoUpAB" x1="120" y1="280" x2="340" y2="280" />
              <line id="demoUpBC" x1="340" y1="280" x2="570" y2="280" />
              <line id="demoUpCD" x1="570" y1="280" x2="840" y2="280" />
            </g>

            <g class="demo-crossovers">
              <path id="demoLeftSwitch" d="M120 280 L180 160" />
              <path d="M120 160 L180 280" />
              <path id="demoRightSwitch" d="M780 280 L840 160" />
              <path d="M780 160 L840 280" />
            </g>

            <g class="demo-platforms" aria-hidden="true">
              <line x1="96" y1="140" x2="166" y2="140" /><line x1="96" y1="300" x2="166" y2="300" />
              <line x1="305" y1="140" x2="375" y2="140" /><line x1="305" y1="300" x2="375" y2="300" />
              <line x1="535" y1="140" x2="605" y2="140" /><line x1="535" y1="300" x2="605" y2="300" />
              <line x1="805" y1="140" x2="865" y2="140" /><line x1="805" y1="300" x2="865" y2="300" />
            </g>

            <g class="demo-stations">
              <g transform="translate(120 0)"><circle cy="160" r="6" /><circle cy="280" r="6" /><text y="352">站A</text><text class="demo-station-role" y="372">起点 / 终点</text></g>
              <g transform="translate(340 0)"><circle cy="160" r="6" /><circle cy="280" r="6" /><text y="352">站B</text><text class="demo-station-role" y="372">中间站</text></g>
              <g transform="translate(570 0)"><circle cy="160" r="6" /><circle cy="280" r="6" /><text y="352">站C</text><text class="demo-station-role" y="372">中间站</text></g>
              <g transform="translate(840 0)"><circle cy="160" r="6" /><circle cy="280" r="6" /><text y="352">站D</text><text class="demo-station-role" y="372">折返站</text></g>
            </g>

            <g class="demo-switch-labels">
              <text x="158" y="225">A端折返道岔</text>
              <text x="802" y="225">D端折返道岔</text>
            </g>

            <g class="demo-signals" aria-hidden="true">
              <g transform="translate(744 130)"><rect x="-7" y="-14" width="14" height="28" rx="3"/><circle id="demoSignalDown" cy="-5" r="4"/><circle cy="6" r="4"/></g>
              <g transform="translate(744 310)"><rect x="-7" y="-14" width="14" height="28" rx="3"/><circle cy="-5" r="4"/><circle id="demoSignalUp" cy="6" r="4"/></g>
            </g>

            <g id="demoTrain" class="demo-train" transform="translate(120 160)">
              <rect x="-30" y="-16" width="60" height="32" rx="4" />
              <path class="demo-train-cab" d="M18 -11 L29 -5 L29 11 L18 11 Z" />
              <text y="5">1车</text>
              <text id="demoTrainDirection" class="demo-train-direction" y="-27">下行 →</text>
            </g>
          </svg>
          <div class="demo-map-legend" aria-label="图例">
            <span><i class="route"></i>锁闭进路</span><span><i class="occupied"></i>列车占用</span><span><i class="switch"></i>反位道岔</span>
          </div>
        </div>
      </section>

      <aside class="demo-map-side">
        <div class="pane-title"><h2>当前运行逻辑</h2><span id="demoMapProgress">1 / 14</span></div>
        <div class="demo-map-state" aria-live="polite">
          <strong id="demoMapPhase">待发</strong>
          <p id="demoMapDescription">站A完成交路设置，等待发车命令。</p>
        </div>
        <dl class="demo-map-readout">
          <div><dt>运行方向</dt><dd id="demoMapDirection">下行</dd></div>
          <div><dt>下一站</dt><dd id="demoMapTarget">站B</dd></div>
          <div><dt>D端道岔</dt><dd id="demoMapSwitch">定位</dd></div>
          <div><dt>进路状态</dt><dd id="demoMapRoute">等待办理</dd></div>
        </dl>
        <ol class="demo-logic-flow" id="demoLogicFlow">
          <li data-logic="route"><i></i><span><b>办理进路</b><small>检查占用并锁闭区段</small></span></li>
          <li data-logic="run"><i></i><span><b>区间运行</b><small>ATO按目标站控制列车</small></span></li>
          <li data-logic="dwell"><i></i><span><b>停站作业</b><small>停稳、开门并计时</small></span></li>
          <li data-logic="turnback"><i></i><span><b>折返换端</b><small>方向反转并切换返程股道</small></span></li>
          <li data-logic="return"><i></i><span><b>生成返程进路</b><small>plan_step 由 +1 变为 −1</small></span></li>
        </ol>
        <p class="demo-map-note">当前后端实现为“换端后对齐返程站台股道”；地图用动画表达换股道过程，不代表完整的道岔动力学仿真。</p>
      </aside>
    </div>`;

  const phases = [
    { name: "待发", description: "站A完成交路设置，等待发车命令。", duration: 900, from: [120, 160], to: [120, 160], direction: 1, target: "站B", route: "等待办理", logic: "route" },
    { name: "进路锁闭", description: "联锁确认A—B区段空闲，锁闭下行进路。", duration: 900, from: [120, 160], to: [120, 160], direction: 1, target: "站B", route: "A—B 已锁闭", segment: "demoDownAB", logic: "route" },
    { name: "区间运行", description: "列车沿下行线驶向站B。", duration: 2200, from: [120, 160], to: [340, 160], direction: 1, target: "站B", route: "A—B 已锁闭", segment: "demoDownAB", logic: "run" },
    { name: "站B停站", description: "列车停稳开门，完成站B停站作业。", duration: 900, from: [340, 160], to: [340, 160], direction: 1, target: "站C", route: "准备延伸", logic: "dwell" },
    { name: "区间运行", description: "进路延伸至站C，列车继续下行。", duration: 2200, from: [340, 160], to: [570, 160], direction: 1, target: "站C", route: "B—C 已锁闭", segment: "demoDownBC", logic: "run" },
    { name: "站C停站", description: "列车停稳开门，完成站C停站作业。", duration: 900, from: [570, 160], to: [570, 160], direction: 1, target: "站D", route: "准备延伸", logic: "dwell" },
    { name: "驶向折返站", description: "锁闭C—D区段，列车驶入站D。", duration: 2500, from: [570, 160], to: [840, 160], direction: 1, target: "站D", route: "C—D 已锁闭", segment: "demoDownCD", logic: "run" },
    { name: "终点停稳", description: "列车在站D停稳，释放到达进路并执行停站计时。", duration: 1200, from: [840, 160], to: [840, 160], direction: 1, target: "—", route: "到达进路释放", logic: "dwell" },
    { name: "道岔反位", description: "D端折返道岔转为反位，返程进路开始锁闭。", duration: 1100, from: [840, 160], to: [840, 160], direction: -1, target: "站C", route: "折返进路锁闭", switchReverse: true, logic: "turnback" },
    { name: "折返换端", description: "原尾车成为新头车，列车切换到上行股道。", duration: 1800, from: [840, 160], to: [780, 280], direction: -1, target: "站C", route: "折返进路锁闭", switchReverse: true, logic: "turnback" },
    { name: "返程运行", description: "plan_step切换为−1，列车沿上行线返回站C。", duration: 2500, from: [780, 280], to: [570, 280], direction: -1, target: "站C", route: "D—C 已锁闭", segment: "demoUpCD", logic: "return" },
    { name: "返程停站", description: "列车到达站C，完成返程停站作业。", duration: 900, from: [570, 280], to: [570, 280], direction: -1, target: "站B", route: "准备延伸", logic: "dwell" },
    { name: "返程运行", description: "列车依次通过站B，继续驶向站A。", duration: 3200, from: [570, 280], to: [120, 280], direction: -1, target: "站A", route: "C—A 已锁闭", segment: "demoUpBC demoUpAB", logic: "return" },
    { name: "循环完成", description: "列车到达站A；下一次折返可按相同逻辑继续循环。", duration: 1000, from: [120, 280], to: [120, 280], direction: -1, target: "—", route: "进路释放", logic: "dwell" }
  ];

  let frame = 0;
  let running = false;
  let phaseIndex = 0;
  let phaseStartedAt = 0;
  let elapsedBeforePhase = 0;

  function element(id) { return document.getElementById(id); }
  function ease(value) { return 1 - Math.pow(1 - value, 4); }
  function phaseElapsedTotal(index, currentElapsed) {
    return phases.slice(0, index).reduce((sum, phase) => sum + phase.duration, 0) + currentElapsed;
  }

  function updateStaticState(phase) {
    element("demoMapPhase").textContent = phase.name;
    element("demoMapDescription").textContent = phase.description;
    element("demoMapDirection").textContent = phase.direction > 0 ? "下行" : "上行";
    element("demoMapTarget").textContent = phase.target;
    element("demoMapSwitch").textContent = phase.switchReverse ? "反位 / 锁闭" : "定位";
    element("demoMapRoute").textContent = phase.route;
    element("demoMapProgress").textContent = `${phaseIndex + 1} / ${phases.length}`;
    element("demoTrainDirection").textContent = phase.direction > 0 ? "下行 →" : "← 上行";

    document.querySelectorAll("#demoMapSvg .route-locked").forEach(node => node.classList.remove("route-locked"));
    if (phase.segment) phase.segment.split(" ").forEach(id => element(id)?.classList.add("route-locked"));
    element("demoRightSwitch")?.classList.toggle("switch-reverse", Boolean(phase.switchReverse));
    element("demoSignalDown")?.classList.toggle("signal-go", phase.direction > 0 && phase.logic === "run");
    element("demoSignalUp")?.classList.toggle("signal-go", phase.direction < 0 && ["turnback", "return"].includes(phase.logic));
    document.querySelectorAll("#demoLogicFlow li").forEach(item => item.classList.toggle("active", item.dataset.logic === phase.logic));
  }

  function render(progress = 0) {
    const root = element("demoMapSvg");
    if (!root) return false;
    const phase = phases[phaseIndex];
    const amount = ease(Math.max(0, Math.min(1, progress)));
    const x = phase.from[0] + (phase.to[0] - phase.from[0]) * amount;
    const y = phase.from[1] + (phase.to[1] - phase.from[1]) * amount;
    const train = element("demoTrain");
    train.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${phase.direction} 1)`);
    element("demoTrainDirection").setAttribute("transform", `scale(${phase.direction} 1)`);
    updateStaticState(phase);
    const total = phaseElapsedTotal(phaseIndex, phase.duration * progress) / 1000;
    element("demoMapClock").textContent = `00:${total.toFixed(1).padStart(4, "0")}`;
    return true;
  }

  function stop() {
    if (running && phaseStartedAt) {
      elapsedBeforePhase = Math.max(0, performance.now() - phaseStartedAt);
    }
    running = false;
    cancelAnimationFrame(frame);
    const button = element("demoMapPlay");
    if (button) button.textContent = phaseIndex === phases.length - 1 ? "重新演示" : "继续演示";
  }

  function tick(timestamp) {
    if (!running || !element("demoMapSvg")) return stop();
    if (!phaseStartedAt) phaseStartedAt = timestamp - elapsedBeforePhase;
    const elapsed = timestamp - phaseStartedAt;
    const phase = phases[phaseIndex];
    render(elapsed / phase.duration);
    if (elapsed >= phase.duration) {
      if (phaseIndex >= phases.length - 1) { render(1); stop(); return; }
      phaseIndex += 1;
      phaseStartedAt = timestamp;
      elapsedBeforePhase = 0;
      render(0);
    }
    frame = requestAnimationFrame(tick);
  }

  function play() {
    if (running) { stop(); return; }
    if (phaseIndex === phases.length - 1) reset();
    running = true;
    phaseStartedAt = 0;
    element("demoMapPlay").textContent = "暂停演示";
    frame = requestAnimationFrame(tick);
  }

  function step() {
    stop();
    phaseIndex = Math.min(phases.length - 1, phaseIndex + 1);
    phaseStartedAt = 0;
    elapsedBeforePhase = 0;
    render(0);
  }

  function reset() {
    stop();
    phaseIndex = 0;
    phaseStartedAt = 0;
    elapsedBeforePhase = 0;
    render(0);
    element("demoMapPlay").textContent = "开始演示";
  }

  function bind() {
    cancelAnimationFrame(frame);
    running = false;
    phaseIndex = 0;
    element("demoMapPlay")?.addEventListener("click", play);
    element("demoMapStep")?.addEventListener("click", step);
    element("demoMapReset")?.addEventListener("click", reset);
    render(0);
  }

  global.DemoMap = Object.freeze({ template, bind });
}(window));
