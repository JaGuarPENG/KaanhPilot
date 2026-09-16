"use strict";
(() => {
  const config = window.REACH_CONFIG;
  const cards = [...document.querySelectorAll(".drink-card")];
  const grids = [...document.querySelectorAll(".category-panel")];
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const byId = id => document.getElementById(id);
  const categorySwitch = document.querySelector(".category-switch");
  const stop = byId("stop-button");
  let state = null, online = false, writing = false, uncertain = false;
  let activeCategory = "drinks", trackedTask = null, requestId = null, syncing = null;
  let lastTerminal = null;
  let observedItem = null, cameraPhase = "idle", menuOpen = false;
  const workspace = byId("workspace"), selectionStage = byId("selection-stage");
  const menuToggle = byId("product-menu-toggle"), returnProducts = byId("return-products");
  let testingStock = false;
  const phases = {sending:"正在发送请求", queued:"等待抓取", running:"正在抓取", stopping:"正在停止",
    recognizing:"正在拍照识别", recognized:"识别已完成", not_detected:"未检测到矿泉水", checking:"正在确认库存", picking:"正在抓取", placed:"放置已完成", completed:"抓取已完成", stopped:"任务已停止", failed:"抓取未完成", needs_attention:"等待确认", offline:"连接恢复中"};

  function observe(itemId, phase) {
    if (!cards.some(card => card.dataset.item === itemId)) return;
    const entering = observedItem === null;
    const focusWasInPicker = selectionStage.contains(document.activeElement);
    if (observedItem !== itemId) menuOpen = false;
    observedItem = itemId;
    cameraPhase = phase;
    renderCameraWorkspace();
    if (entering || (focusWasInPicker && !menuOpen)) menuToggle.focus({preventScroll:true});
    if (entering && window.scrollY > 120) workspace.scrollIntoView({behavior:"auto", block:"start"});
  }
  function renderCameraWorkspace() {
    const observing = observedItem !== null;
    workspace.classList.toggle("is-observing", observing);
    byId("capture-menu").hidden = !observing;
    byId("camera-workspace").hidden = !observing;
    selectionStage.classList.toggle("is-open", menuOpen);
    selectionStage.inert = observing && !menuOpen;
    selectionStage.setAttribute("aria-hidden", String(observing && !menuOpen));
    menuToggle.setAttribute("aria-expanded", String(menuOpen));
    grids.forEach(panel => {panel.hidden = observing ? false : panel.dataset.category !== activeCategory;});
    returnProducts.disabled = busy();
    if (!observing) return;
    const card = cards.find(card => card.dataset.item === observedItem);
    const thumbnail = card.querySelector(".drink-image");
    byId("capture-thumbnail").className = thumbnail.className + " capture-thumbnail";
    byId("capture-item").textContent = card.querySelector(".drink-name").textContent;
    const phase = online ? cameraPhase : "offline";
    const simulationPhases = {checking:"模拟库存确认", picking:"正在模拟拿取", placed:"模拟放置完成", completed:"模拟拿取已完成"};
    byId("capture-phase").textContent = (state?.dry_run ? simulationPhases[phase] : null) || phases[phase] || "等待确认";
    byId("capture-indicator").dataset.phase = phase;
    cards.forEach(item => item.classList.toggle("is-current", item.dataset.item === observedItem));
  }
  menuToggle.addEventListener("click", () => {menuOpen = !menuOpen; renderCameraWorkspace();});
  selectionStage.addEventListener("keydown", event => {
    if (event.key === "Escape" && observedItem) {
      menuOpen = false; renderCameraWorkspace(); menuToggle.focus();
    }
  });
  returnProducts.addEventListener("click", () => {
    if (busy()) return;
    const selectedCard = cards.find(card => card.dataset.item === observedItem);
    observedItem = null; menuOpen = false;
    cards.forEach(card => card.classList.remove("is-current"));
    if (selectedCard) setCategory(selectedCard.closest(".category-panel").dataset.category);
    render();
    selectedCard?.focus({preventScroll:true});
  });
  // 每个画面单独配置；接入图片/MJPEG 或视频时仅替换 config.js 中的源地址。
  document.querySelectorAll("[data-camera]").forEach(frame => {
    const source = config.cameras?.[frame.dataset.camera];
    if (!source?.src) return;
    const original = frame.querySelector(".camera-image");
    if (source.demo === false) original.alt = ({head:"头部", left:"左手", right:"右手"}[frame.dataset.camera]) + "相机画面";
    const media = source.type === "video" ? document.createElement("video") : original;
    if (media !== original) {
      media.className = "camera-image";
      media.muted = true; media.autoplay = true; media.loop = source.demo !== false;
      media.playsInline = true; media.controls = true;
      media.setAttribute("aria-label", original.alt);
      original.replaceWith(media);
    }
    media.addEventListener("error", () => {frame.querySelector(".camera-error").hidden = false;});
    media.addEventListener(source.type === "video" ? "loadeddata" : "load", () => {frame.querySelector(".camera-error").hidden = true;});
    if (source.type === "snapshot") {
      let objectUrl = null;
      const refresh = async () => {
        if (observedItem !== null && !document.hidden) {
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), 5000);
          try {
            const response = await fetch(config.apiBase.replace(/\/$/, "") + source.src,
              {cache:"no-store", credentials:"same-origin", signal:controller.signal});
            if (!response.ok || !response.headers.get("Content-Type")?.startsWith("image/jpeg")) throw new Error("相机不可用");
            const nextUrl = URL.createObjectURL(await response.blob());
            media.src = nextUrl;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = nextUrl;
          } catch (error) {
            frame.querySelector(".camera-error").hidden = false;
          } finally {clearTimeout(timeout);}
        }
        setTimeout(refresh, source.refreshMs || 200);
      };
      media.removeAttribute("src");
      frame.querySelector(".camera-error").hidden = false;
      refresh();
    } else {
      media.src = source.src;
    }
    frame.querySelector(".placeholder-note").textContent = source.demo === false ? "相机画面" : "示意画面 · 非实时";
  });
  const allCameraSources = ["head", "left", "right"].map(key => config.cameras?.[key]);
  if (allCameraSources.every(source => source?.demo === false)) {
    document.querySelector(".demo-label").lastChild.textContent = "相机画面";
    document.querySelector(".camera-footnote p span").textContent = "画面由相机源提供。";
  }
  if (config.cameras?.head?.demo === false) {
    document.querySelector(".demo-label").lastChild.textContent = "头部相机 · 手部示意";
    document.querySelector(".camera-footnote p span").textContent = "头部为实时相机画面，左右手为示意画面。";
  }
  const terminal = new Set(["completed", "stopped", "failed"]);
  const busy = () => writing || testingStock || uncertain || Boolean(state?.active_task);

  function status(title, detail, kind = "idle") {
    byId("status-title").textContent = title;
    byId("status-detail").textContent = detail;
    const symbol = byId("status-symbol");
    symbol.className = "status-symbol " + kind;
    symbol.innerHTML = kind === "idle" ? '<span class="idle-symbol"></span>' : kind === "success" ? "✓" : kind === "error" ? "!" : "";
  }
  function step(number) {
    byId("progress-fill").style.background = "";
    document.querySelectorAll("[data-step]").forEach(el => {
      if (el.dataset.step === "3") el.textContent = "放置完成";
    });
    byId("task-progress").hidden = number === 0;
    byId("progress-fill").style.width = [0,12,57,100][number] + "%";
    document.querySelectorAll("[data-step]").forEach(el => el.classList.toggle("active",Number(el.dataset.step) <= number));
  }
  function render() {
    const locked = busy();
    renderCameraWorkspace();
    cards.forEach(card => {
      const item = state?.items[card.dataset.item];
      const soldOut = item?.available === 0;
      card.disabled = locked || !online || !state?.ready || item?.available !== 1;
      card.classList.toggle('sold-out', soldOut);
      card.setAttribute('aria-label', (item?.name || card.querySelector('.drink-name').textContent) + (soldOut ? '，已售尽' : '，点击抓取'));
      const selected = state?.active_task?.item_id === card.dataset.item;
      card.classList.toggle("selected",selected);
      card.setAttribute("aria-pressed",String(selected));
      card.querySelector(".card-action").innerHTML = soldOut ? "已售尽" : '点击抓取 <span class="arrow" aria-hidden="true">↗</span>';
    });
    byId('inventory-test').hidden = !online || state?.dry_run !== true;
    byId('inventory-test-form').querySelectorAll('select, button').forEach(el => {el.disabled = locked || !online;});
    grids.forEach(grid => grid.classList.toggle("busy",locked));
    tabs.forEach(tab => {tab.disabled = locked;});
    categorySwitch.setAttribute("aria-busy",String(locked));
    stop.disabled = state?.mode === "real" || writing || !online || !(state?.active_task || uncertain);
    byId("connection-label").textContent = !online ? "连接中断" : state.dry_run === true ? "模拟模式" : state.ready ? "设备已就绪" : "设备未就绪";
    byId("connection").className = "connection " + (online ? "online" : "error");
    // 模式说明是可选元素；HTML 删除它后，状态轮询仍须正常启动。
    const modeNote = byId("mode-note");
    if (modeNote) {
      modeNote.textContent = state?.mode === "real"
        ? "演示 · 网页停止不可用，请使用控制器停止；状态不明时暂停拿取。"
        : "服务端演示 · 任务已持久保存";
    }
  }
  function setCategory(category, resetStatus = true) {
    if (busy() || category === activeCategory) return;
    activeCategory = category;
    categorySwitch.dataset.category = category;
    tabs.forEach(tab => {
      const selected = tab.dataset.category === category;
      tab.setAttribute("aria-selected",String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    grids.forEach(panel => {panel.hidden = panel.dataset.category !== category;});
    byId("page-title").textContent = category === "drinks" ? "想喝点什么？" : "想吃点什么？";
    byId("category-description").textContent = "选择一款" + (category === "drinks" ? "饮料" : "零食") + "，即可发送抓取指令。";
    if (resetStatus && online && state.ready) {step(0);status("准备好，为你取来。","选择喜欢的饮料或零食。");}
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(),config.requestTimeoutMs);
    try {
      const response = await fetch(config.apiBase.replace(/\/$/,"") + path, {
        method: body ? "POST" : "GET", headers: body ? {"Content-Type":"application/json"} : {},
        body: body ? JSON.stringify(body) : undefined, signal: controller.signal,
        credentials: "same-origin", cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) {const error = new Error(data.error || "请求失败"); error.code = response.status; error.errorCode = data.error_code; throw error;}
      return data;
    } finally {clearTimeout(timeout);}
  }
  function accept(data) {
    if (!data || !Number.isInteger(data.revision) || !["demo","real"].includes(data.mode)
        || typeof data.dry_run !== "boolean" || typeof data.ready !== "boolean" || !("active_task" in data)
        || !cards.every(card => {
          const item = data.items?.[card.dataset.item];
          return item && typeof item.name === "string" && [0,1,null].includes(item.available);
        })) throw new Error("设备状态接口格式不正确");
    if (state && data.revision < state.revision) return;
    state = data;
    online = true;
  }
  function showTask(task) {
    if (!task || typeof task.task_id !== "string") throw new Error("任务接口格式不正确");
    observe(task.item_id, task.recognition ? task.phase : terminal.has(task.status) ? task.status : task.phase || task.status);
    if (terminal.has(task.status)) {
      if (lastTerminal !== task.task_id) {
        const name = state.items[task.item_id]?.name || "物品";
        step(task.status === "completed" ? 3 : task.status === "failed" ? (task.phase === "checking" ? 1 : 2) : 0);
        if (task.status === "failed") {
          byId("progress-fill").style.background = "#b54637";
        }
        status(task.status === "completed" ? (state.dry_run ? "模拟拿取完成 · " : "抓取完毕 · ") + name : task.status === "stopped" ? "任务已停止。" : "抓取未完成。",
          task.status === "completed" ? (state.dry_run ? "模拟流程已完成，未执行机器人动作。" : "动作已收到控制器完成回复。") : "任务已结束，请以设备实际状态为准。",
          task.status === "completed" ? "success" : task.status === "failed" ? "error" : "idle");
        if (task.recognition) {
          const found = task.recognition.detected === true;
          byId("task-progress").hidden = true;
          status(found ? "矿泉水识别完成。" : "未检测到矿泉水。",
            found ? "已完成两次拍照识别，可继续查看头部相机画面。" : "请调整商品位置或相机视角后重试。",
            found ? "success" : "error");
        }
        if (task.error_code === 'out_of_stock') {
          cameraPhase = 'failed';
          status(name + '已售尽。', '本次未进行抓取，请选择其他商品。', 'error');
          showSoldOut(task.item_id);
        }
        lastTerminal = task.task_id;
      }
      trackedTask = null; requestId = null; uncertain = false;
      return;
    }
    trackedTask = task.task_id;
    requestId = task.request_id;
    uncertain = false;
    step(task.phase === "checking" ? 1 : task.status === "running" ? 2 : 1);
    if (task.status === "needs_attention") {
      status("任务结果待确认。", "正在同步设备状态，请勿重复发送抓取指令。", "error");
      return;
    }
    if (task.phase === "recognizing") {
      byId("task-progress").hidden = true;
      status("正在拍照识别矿泉水。", "相机将进行两次识别，请保持商品静止。", "working");
      return;
    }
    status(task.phase === "checking" ? "正在确认库存。" : task.status === "running" ? "抓取进行中。" : task.status === "stopping" ? "正在等待停止确认。" : "指令已接收。",
      state.dry_run ? "正在模拟拿取流程，头部视角可查看已连接的本机相机。" : "等待控制器完成回复。","working");
  }
  async function sync() {
    if (syncing) return syncing;
    syncing = (async () => {
      try {
        accept(await api(config.statusPath));
        if (!writing && !testingStock) {
          if (state.active_task) showTask(state.active_task);
          else if (uncertain && requestId) {
            try {
              const task = await api("/api/requests/" + encodeURIComponent(requestId));
              accept(await api(config.statusPath));
              showTask(task);
            } catch (error) {if (error.code !== 404) throw error;}
          }
          else if (trackedTask) {
            const task = await api(config.taskPath.replace("{taskId}",encodeURIComponent(trackedTask)));
            accept(await api(config.statusPath));
            if (state.active_task) showTask(state.active_task); else showTask(task);
          } else if (!uncertain && !state.ready) status("设备暂未就绪。","请检查服务、设备适配器和安全联锁。","error");
          else if (!uncertain && ["正在连接设备服务。", "设备暂未就绪。"].includes(byId("status-title").textContent)) status("准备好，为你取来。","选择喜欢的饮料或零食。");
        }
      } catch {
        online = false;
        status("正在连接设备服务。","连接恢复前暂停拿取；已发出的任务不会因网页断线自动停止。","error");
      } finally {render();}
    })();
    try {await syncing;} finally {syncing = null;}
  }
  function newRequestId() {
    // HTTP 局域网 iPad 也可用，不依赖 randomUUID 的安全上下文。
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, n => n.toString(16).padStart(2,"0")).join("");
  }
  async function grab(id) {
    if (busy() || !online || !state.ready || state.items[id]?.available !== 1) return;
    writing = true; requestId = newRequestId();
    menuOpen = false; observe(id, "sending"); render();step(1);
    status("正在发送抓取指令。","等待服务器检查设备状态。","working");
    try {showTask(await api(config.grabPath,{item_id:id,request_id:requestId}));}
    catch (error) {
      uncertain = !(error.code >= 400 && error.code < 500);
      if (error.errorCode === 'out_of_stock') showSoldOut(id);
      if (uncertain) cameraPhase = "needs_attention";
      else {
        observedItem = null; menuOpen = false;
        cards.forEach(card => card.classList.remove("is-current"));
      }
      status(uncertain ? "暂时无法确认指令。" : "暂时无法拿取。",
        uncertain ? "已暂停新请求，正在同步服务器任务；不要重复发送。" : error.message,"error");
    } finally {
      writing = false; await sync(); render();
      if (!observedItem && !uncertain && !busy()) {
        const card = cards.find(item => item.dataset.item === id);
        if (card) {
          // 明确拒绝后恢复来源分类，同时保留错误提示。
          setCategory(card.closest(".category-panel").dataset.category, false);
          render();
          if (!card.disabled) card.focus({preventScroll:true});
        }
      }
    }
  }
  function showSoldOut(id) {
    const item = state?.items[id];
    if (item) item.available = 0;
    if (byId('inventory-test-item').value === id) {
      byId('inventory-test-feedback').textContent = (item?.name || '该商品') + ' · 已确认售尽，可选择“有库存”恢复测试。';
    }
    byId('sold-out-title').textContent = (item?.name || '该商品') + '已售尽';
    const dialog = byId('sold-out-dialog');
    if (!dialog.open) dialog.showModal();
  }
  byId('sold-out-dialog').addEventListener('close', () => {
    if (busy()) return;
    const card = cards.find(item => item.dataset.item === observedItem);
    observedItem = null; menuOpen = false;
    cards.forEach(item => item.classList.remove('is-current'));
    if (card) setCategory(card.closest('.category-panel').dataset.category, false);
    render();
    const next = cards.find(item => !item.disabled && !item.closest('.category-panel').hidden);
    (next || tabs.find(tab => tab.getAttribute('aria-selected') === 'true'))?.focus({preventScroll:true});
  });
  byId('inventory-test-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy() || !online || state?.dry_run !== true) return;
    const itemId = byId('inventory-test-item').value;
    const scenario = byId('inventory-test-scenario').value;
    testingStock = true; render();
    byId('inventory-test-feedback').textContent = '正在更新…';
    try {
      accept(await api('/api/test/inventory', {
        item_id:itemId, available:scenario === 'sold-out' ? 0 : 1,
        confirmation_available:scenario === 'available' ? 1 : 0
      }));
      byId('inventory-test-feedback').textContent = state.items[itemId].name + ' · ' +
        (scenario === 'confirmation-empty' ? '下次确认将发现售尽，当前仍可点击。' : scenario === 'sold-out' ? '已设为售尽。' : '已设为有库存。');
    } catch (error) {
      byId('inventory-test-feedback').textContent = error.message || '更新失败，请重试。';
    } finally {testingStock = false; await sync(); render();}
  });
  stop.addEventListener("click",async () => {
    if (stop.disabled) return;
    writing = true;render();
    try {showTask(await api(config.stopPath,{task_id:state?.active_task?.task_id || trackedTask,request_id:requestId}));}
    catch {status("无法确认停止。","不要依赖网页停止按钮；请检查设备，紧急时使用物理急停。","error");}
    finally {writing = false;await sync();render();}
  });
  cards.forEach(card => card.addEventListener("click",() => grab(card.dataset.item)));
  tabs.forEach((tab,index) => {
    tab.addEventListener("click",() => setCategory(tab.dataset.category));
    tab.addEventListener("keydown",event => {
      if (busy() || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      setCategory(tabs[target].dataset.category);tabs[target].focus();
    });
  });
  status("正在连接设备服务。","请确认已使用 python3 server.py 启动本地服务。","working");
  render();
  async function loop() {await sync();setTimeout(loop,config.pollIntervalMs);}
  loop();
})();
