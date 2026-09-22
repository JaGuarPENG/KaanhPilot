"use strict";
(() => {
  const config = window.REACH_CONFIG;
  const cards = [...document.querySelectorAll(".drink-card")];
  const grids = [...document.querySelectorAll(".category-panel")];
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const byId = id => document.getElementById(id);
  const categorySwitch = document.querySelector(".category-switch");
  const categories = {
    snacks: {title:"想吃点什么？", description:"选择一款零食，即可发送抓取指令。"},
    drinks: {title:"想喝点什么？", description:"选择一款饮料，即可发送抓取指令。"},
    coffee: {title:"来杯咖啡吧。", description:"选择喜欢的风味，开启一刻咖啡时光。"}
  };
  const stop = byId("stop-button");
  let state = null, online = false, writing = false, uncertain = false;
  let activeCategory = "drinks", trackedTask = null, syncing = null;
  let browsingProducts = false;
  let observedItem = null, cameraPhase = "idle", menuOpen = false;
  const workspace = byId("workspace"), selectionStage = byId("selection-stage");
  const menuToggle = byId("product-menu-toggle"), returnProducts = byId("return-products");
  let testingStock = false;
  const phases = {pick:"正在抓取", transport_to_dropoff:"正在运输", place:"正在放置", return_and_reset:"返回复位", paused:"等待处理", cancelling:"正在回位", succeeded:"取送完成", cancelled:"已取消", sending:"正在发送请求", queued:"等待抓取", running:"正在抓取", stopping:"正在停止",
    recognizing:"正在拍照识别", recognized:"识别已完成", not_detected:"未检测到矿泉水", checking:"正在确认库存", picking:"正在抓取", placed:"放置已完成", completed:"抓取已完成", stopped:"任务已停止", failed:"抓取未完成", needs_attention:"等待确认", offline:"连接恢复中"};

  function observe(itemId, phase) {
    if (!cards.some(card => card.dataset.item === itemId)) return;
    const entering = observedItem === null;
    const focusWasInPicker = selectionStage.contains(document.activeElement);
    if (observedItem !== itemId) menuOpen = true;
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
    document.querySelector('.demo-label').lastChild.textContent = state?.camera_available ? '左手相机 · 头部、右手示意' : '示意画面';
    document.querySelector('.camera-footnote p span').textContent = state?.camera_available ? '左手为实时相机画面。头部、右手为示意画面。' : '当前运行时未提供相机，以下为示意画面。';
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
    observedItem = null; menuOpen = false; browsingProducts = true;
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
        if (state && !state.camera_available) {
          if (!media.getAttribute('src')?.startsWith('assets/')) media.src = `assets/camera-${frame.dataset.camera}.svg`;
          media.alt = '相机示意画面';
          frame.querySelector('.camera-error').hidden = true;
          frame.querySelector('.placeholder-note').textContent = '示意画面 · 非实时';
        }
        if (observedItem !== null && !document.hidden && online && state?.camera_available) {
          frame.querySelector('.placeholder-note').textContent = '相机画面';
          media.alt = '左手相机画面';
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
  const cameraNames = {head:"头部", left:"左手", right:"右手"};
  const liveViews = Object.keys(cameraNames).filter(key => config.cameras?.[key]?.demo === false);
  const demoViews = Object.keys(cameraNames).filter(key => !liveViews.includes(key));
  if (liveViews.length) {
    const liveLabel = liveViews.map(key => cameraNames[key]).join("、");
    const demoLabel = demoViews.map(key => cameraNames[key]).join("、");
    document.querySelector(".demo-label").lastChild.textContent = liveLabel + "相机" + (demoLabel ? " · " + demoLabel + "示意" : "");
    document.querySelector(".camera-footnote p span").textContent = liveLabel + "为实时相机画面。" + (demoLabel ? demoLabel + "为示意画面。" : "");
  }

  const model = window.QueueModel;
  const terminal = new Set(['succeeded', 'failed', 'cancelled']);
  const busy = () => writing || testingStock || uncertain;
  let selectedOrder = null, pendingRequest = null;
  const orders = new Map(), aliases = new Map(), stock = {};
  const products = cards.map(card => ({id:card.dataset.item, name:card.querySelector('.drink-name').textContent}));
  let runtimeId = null;
  try {pendingRequest = JSON.parse(sessionStorage.getItem('kaanh-direct-pending'));} catch {}
  uncertain = Boolean(pendingRequest);
  function persistPending(value) {
    pendingRequest = value; uncertain = Boolean(value);
    try {
      if (value) sessionStorage.setItem('kaanh-direct-pending', JSON.stringify(value));
      else sessionStorage.removeItem('kaanh-direct-pending');
    } catch {}
  }
  function remember(order) {
    const previous = orders.get(order.order_id);
    if (previous && (previous.updated_at > order.updated_at ||
        (terminal.has(previous.status) && !terminal.has(order.status)))) return previous;
    if (!previous || previous.updated_at !== order.updated_at || previous.status !== order.status) model.updateStock(stock, order);
    const display = {...order, execution_item_id:order.item_id, item_id:aliases.get(order.order_id) || order.item_id,
      allowed_actions:model.canCancel(order) ? ['cancel'] : []};
    orders.set(order.order_id, display);
    // 仅保留有限的本页历史；活动订单一直跟踪到终态。
    const ended = [...orders.values()].filter(value => terminal.has(value.status));
    ended.slice(0, Math.max(0, ended.length - 20)).forEach(value => {
      if (value.order_id !== trackedTask) {orders.delete(value.order_id); aliases.delete(value.order_id);}
    });
    return display;
  }
  function refreshItems() {
    if (state) state.items = model.items(products, config.itemTasks, stock);
  }
  function status(title, detail, kind = 'idle') {
    byId('status-title').textContent = title;
    byId('status-detail').textContent = detail;
    const symbol = byId('status-symbol');
    symbol.className = 'status-symbol ' + kind;
    symbol.textContent = kind === 'success' ? '✓' : kind === 'error' ? '!' : '';
  }
  function step(order) {
    byId('task-progress').hidden = !order;
    const stages = model.stages(order);
    document.querySelectorAll('[data-step]').forEach((el, i) => {
      const stage = stages[i];
      el.dataset.status = stage.status;
      el.replaceChildren(document.createTextNode(stage.label));
      const detail = document.createElement('small');
      detail.textContent = model.statusLabel(stage.status);
      el.append(detail);
    });
    byId('progress-fill').style.width = model.progress(order) + '%';
    byId('progress-fill').style.background = order?.status === 'failed' ? '#b54637' : '';
  }
  function orderRow(order, index, history = false) {
    const row = document.createElement('li');
    row.className = 'queue-row' + (selectedOrder?.order_id === order.order_id ? ' is-selected' : '');
    const select = document.createElement('button');
    select.className = 'queue-select';
    const name = state.items[order.item_id]?.name || order.item_id;
    select.textContent = (history ? '' : index === 0 && order.status !== 'queued' ? '当前 · ' : '等待 · ') + name;
    const detail = document.createElement('small');
    detail.textContent = model.statusLabel(order.status) + ' · ' + order.order_id.slice(0, 8);
    select.append(detail);
    select.addEventListener('click', () => {browsingProducts = false; selectedOrder = order; trackedTask = order.order_id; showTask(order); render();});
    row.append(select);
    if (order.allowed_actions?.includes('cancel')) {
      const cancel = document.createElement('button');
      cancel.className = 'queue-cancel'; cancel.textContent = order.status === 'paused' ? '取消并回位' : '取消';
      cancel.disabled = writing || !online;
      cancel.addEventListener('click', () => cancelOrder(order.order_id));
      row.append(cancel);
    }
    return row;
  }
  function renderQueue() {
    if (!state) return;
    const queue = state.queue;
    const orders = [...(queue.current_order ? [queue.current_order] : []), ...queue.pending_orders];
    byId('queue-panel').hidden = !orders.length && !state.fatal_error;
    byId('queue-count').textContent = `等待 ${queue.pending_count} / ${queue.capacity}`;
    byId('queue-list').replaceChildren(...orders.map((order, i) => orderRow(order, i)));
    const note = byId('queue-note');
    note.textContent = state.fatal_error ? '设备故障：' + state.fatal_error :
      queue.current_order?.status === 'paused' ? '当前订单等待处理，后续订单暂不执行。' : '';
    note.hidden = !note.textContent;
    byId('recent-orders').replaceChildren(...state.recent_orders.filter(order => terminal.has(order.status)).slice(0, 10).map(order => orderRow(order, 0, true)));
  }
  function render() {
    const locked = busy();
    refreshItems();
    renderCameraWorkspace();
    cards.forEach(card => {
      const item = state?.items[card.dataset.item];
      const soldOut = item?.available === 0;
      card.disabled = locked || !online || !model.canSubmit(state, card.dataset.item);
      card.classList.toggle('sold-out', soldOut);
      card.setAttribute('aria-label', (item?.name || card.querySelector('.drink-name').textContent) + (soldOut ? '，已售尽' : '，加入队列'));
      const selected = state?.queue.current_order?.item_id === card.dataset.item;
      card.classList.toggle('selected', selected); card.setAttribute('aria-pressed', String(selected));
      card.querySelector('.card-action').textContent = soldOut ? '已售尽' : '加入队列 ↗';
    });
    byId('inventory-test').hidden = !online || state?.inventory_test !== true;
    const occupied = Boolean(state?.queue.current_order || state?.queue.pending_count);
    byId('inventory-test-form').querySelectorAll('select, button').forEach(el => {el.disabled = locked || occupied || !online;});
    byId('restock-panel').hidden = !online;
    byId('uncertain-panel').hidden = !uncertain;
    byId('uncertain-confirm').disabled = writing || !online;
    byId('restock-form').querySelectorAll('select, button').forEach(el => {el.disabled = locked || occupied || !online;});
    grids.forEach(grid => grid.classList.toggle('busy', locked));
    tabs.forEach(tab => {tab.disabled = locked;});
    categorySwitch.setAttribute('aria-busy', String(locked));
    stop.disabled = writing || !online || !selectedOrder?.allowed_actions?.includes('cancel');
    stop.textContent = selectedOrder?.status === 'paused' ? '取消并回位' : '取消订单';
    byId('connection-label').textContent = !online ? '连接中断' : state.fatal_error ? '设备故障' : state.dry_run ? '模拟控制器' : state.accepting_orders ? '可以下单' : '暂不接单';
    byId('connection').className = 'connection ' + (online && !state?.fatal_error ? 'online' : 'error');
    renderQueue();
  }
  function setCategory(category, resetStatus = true) {
    if (busy() || !categories[category] || category === activeCategory) return;
    activeCategory = category; categorySwitch.dataset.category = category;
    const activeIndex = tabs.findIndex(tab => tab.dataset.category === category);
    tabs.forEach((tab, index) => {
      const selected = tab.dataset.category === category;
      const offset = (index - activeIndex + tabs.length) % tabs.length;
      tab.dataset.position = offset === 0 ? 'center' : offset === 1 ? 'right' : 'left';
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
    });
    grids.forEach(panel => {panel.hidden = panel.dataset.category !== category;});
    byId('page-title').textContent = categories[category].title;
    byId('category-description').textContent = categories[category].description;
    if (resetStatus && !selectedOrder && online) {step(null); status('准备好，为你取来。', '选择商品，加入任务队列。');}
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs);
    try {
      const response = await fetch(config.apiBase + path, {
        method: body ? 'POST' : 'GET', headers: body ? {'Content-Type':'application/json', 'X-Kaanh-Request':'1'} : {},
        body: body ? JSON.stringify(body) : undefined, signal:controller.signal, credentials:'same-origin', cache:'no-store'
      });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error?.message || data.error || '请求失败'); error.code = response.status; throw error;
      }
      return data;
    } finally {clearTimeout(timeout);}
  }
  function showTask(task) {
    if (!task || typeof task.order_id !== 'string') throw new Error('订单接口格式不正确');
    selectedOrder = task; trackedTask = task.order_id;
    const name = state?.items[task.item_id]?.name || task.item_id;
    const phase = task.status === 'running' ? task.current_task_type : task.status;
    // 不因后台轮询夺走用户正在浏览商品时的焦点或关闭菜单。
    if (!browsingProducts) {
      if (observedItem !== task.item_id) observe(task.item_id, phase);
      else {cameraPhase = phase; renderCameraWorkspace();}
    }
    step(task);
    const stages = {pick:'正在抓取', transport_to_dropoff:'正在运输', place:'正在放置', return_and_reset:'已放置，正在返回复位'};
    const title = task.status === 'running' ? stages[task.current_task_type] || '正在执行' : model.statusLabel(task.status);
    const fallback = task.item_id !== task.execution_item_id ? `当前实际执行${state.items[task.execution_item_id]?.name || task.execution_item_id}任务。` : '';
    let detail = task.message || (task.status === 'queued' ? '已加入队列，可继续选择其他商品。' : '任务状态由设备实时同步。');
    if (task.status === 'paused') detail = task.pause_reason === 'second_detection_failed' ? '第二次识别未找到目标，可取消并回位。' : '目标位置不可达，可取消并回位。';
    if (task.error_code === 'out_of_stock') detail = '未检测到库存，对应商品已暂停下单。';
    status(name + ' · ' + (task.error_code === 'out_of_stock' ? '已售尽' : title), fallback + detail,
      task.status === 'succeeded' ? 'success' : ['failed','paused','needs_attention'].includes(task.status) ? 'error' : terminal.has(task.status) ? 'idle' : 'working');
  }
  async function sync() {
    if (syncing) return syncing;
    syncing = (async () => {
      try {
        const [runnerStatus, queue, info] = await Promise.all([
          api(config.statusPath), api(config.queuePath), api('/api/info')]);
        if (!model.validRunner(runnerStatus, queue) || typeof info.dry_run !== 'boolean' || typeof info.runtime_id !== 'string')
          throw new Error('Runner 接口格式不正确');
        if (runtimeId && runtimeId !== info.runtime_id) {
          orders.clear(); aliases.clear(); Object.keys(stock).forEach(key => delete stock[key]);
          trackedTask = null; selectedOrder = null; step(null);
        }
        runtimeId = info.runtime_id;
        const active = [...(queue.current_order ? [queue.current_order] : []), ...queue.pending_orders];
        const activeIds = new Set(active.map(order => order.order_id));
        // 已离开队列的订单仍需查询，才能看到完成、失败或缺货结果。
        const missing = [...orders.values()].filter(order => !terminal.has(order.status) && !activeIds.has(order.order_id));
        for (const order of missing) {
          try {remember(await api(config.taskPath.replace('{taskId}', encodeURIComponent(order.order_id))));}
          catch (error) {if (error.code === 404) orders.delete(order.order_id); else throw error;}
        }
        state = {...runnerStatus, dry_run:info.dry_run, camera_available:info.camera_available,
          inventory_test:info.inventory_test, fatal_error:runnerStatus.fatal_error_message,
          queue:{capacity:queue.capacity, pending_count:queue.pending_orders.length,
            current_order:queue.current_order ? remember(queue.current_order) : null,
            pending_orders:queue.pending_orders.map(remember)}, recent_orders:[]};
        state.recent_orders = [...orders.values()].reverse();
        refreshItems(); online = true;
        if (!writing) {
          const tracked = orders.get(trackedTask);
          // 默认跟随 Runner 当前订单，提交或取消等待订单不能抢占执行进度。
          if (state.queue.current_order) showTask(state.queue.current_order);
          else if (state.queue.pending_orders.length) showTask(state.queue.pending_orders[0]);
          else if (tracked) showTask(tracked);
          else if (state.recent_orders.length) showTask(state.recent_orders[0]);
          else if (!selectedOrder) status('准备好，为你取来。', '选择商品，加入任务队列。');
        }
        if (state.fatal_error) status('设备已暂停服务。', state.fatal_error, 'error');
        if (uncertain) status('提交结果待核对。', '请求可能已经入队。请核对队列与设备结果，确认后再继续下单。', 'error');
      } catch (error) {
        online = false;
        status(error.code === 401 ? '等待连接设备。' : '连接暂不可用。', error.message, 'error');
      } finally {render();}
    })();
    try {await syncing;} finally {syncing = null;}
  }
  async function grab(id) {
    if (busy() || !online || !model.canSubmit(state, id)) return;
    writing = true; browsingProducts = false;
    // Runner 没有请求去重接口。超时后禁止自动重发。
    persistPending({item_id:id, execution_item_id:config.itemTasks[id]});
    render();
    try {
      const order = await api(config.grabPath, {item_id:config.itemTasks[id]});
      aliases.set(order.order_id, id);
      persistPending(null); remember(order);
    } catch (error) {
      if (error.code >= 400 && error.code < 500) persistPending(null);
      status('暂时无法确认订单。', error.message, 'error');
    } finally {writing = false; await sync(); render();}
  }
  async function cancelOrder(id) {
    if (writing || !online) return;
    writing = true; render();
    try {remember(await api('/api/orders/' + encodeURIComponent(id) + '/cancel', {}));}
    catch (error) {status('取消未完成。', error.message, 'error');}
    finally {writing = false; await sync(); render();}
  }
  stop.addEventListener('click', () => {if (!stop.disabled) cancelOrder(selectedOrder.order_id);});
  cards.forEach(card => card.addEventListener('click', () => grab(card.dataset.item)));
  byId('inventory-test-form').addEventListener('submit', async event => {
    event.preventDefault(); testingStock = true; render();
    try {
      const item = config.itemTasks[byId('inventory-test-item').value];
      await api('/api/test/inventory', {item_id:item, scenario:byId('inventory-test-scenario').value});
      delete stock[item];
      byId('inventory-test-feedback').textContent = '场景已更新。提交下一单后，由 Runner 返回模拟检测结果。';
    } catch (error) {byId('inventory-test-feedback').textContent = error.message;}
    finally {testingStock = false; await sync(); render();}
  });
  byId('restock-item').replaceChildren(...cards.map(card => {
    const option = document.createElement('option'); option.value = card.dataset.item;
    option.textContent = card.querySelector('.drink-name').textContent; return option;
  }));
  byId('restock-form').addEventListener('submit', async event => {
    event.preventDefault(); testingStock = true; render();
    try {delete stock[config.itemTasks[byId('restock-item').value]]; byId('restock-feedback').textContent = '本页已允许重试，下一单由 Runner 重新检测。';}
    catch (error) {byId('restock-feedback').textContent = error.message;}
    finally {testingStock = false; await sync(); render();}
  });
  byId('uncertain-confirm').addEventListener('click', () => {persistPending(null); sync();});
  let drag = null, suppressClickUntil = 0;
  categorySwitch.addEventListener('pointerdown', event => {
    if (busy() || !event.isPrimary || event.button !== 0) return;
    drag = {id:event.pointerId, x:event.clientX, y:event.clientY, dx:0, horizontal:false};
  });
  categorySwitch.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.horizontal && Math.abs(dy) > Math.max(10, Math.abs(dx))) {drag = null; return;}
    if (!drag.horizontal && Math.abs(dx) > 8) {
      drag.horizontal = true;
      categorySwitch.setPointerCapture(event.pointerId);
      categorySwitch.classList.add('is-dragging');
    }
    if (drag.horizontal) {
      drag.dx = dx;
      categorySwitch.style.setProperty('--drag', Math.max(-50, Math.min(50, dx)) + 'px');
    }
  });
  function finishDrag(event) {
    // Touch starts with implicit capture on the tab; transferring it to the
    // wheel also emits lostpointercapture on that child, not a cancelled drag.
    if (event.type === 'lostpointercapture' && event.target !== categorySwitch) return;
    if (!drag || event.pointerId !== drag.id) return;
    const gesture = drag;
    drag = null;
    categorySwitch.classList.remove('is-dragging');
    categorySwitch.style.removeProperty('--drag');
    if (gesture.horizontal) suppressClickUntil = performance.now() + 350;
    if (event.type === 'pointerup' && Math.abs(gesture.dx) >= 30 && !busy()) {
      const index = tabs.findIndex(tab => tab.dataset.category === activeCategory);
      const target = tabs[(index + (gesture.dx < 0 ? 1 : -1) + tabs.length) % tabs.length];
      setCategory(target.dataset.category);
      target.focus({preventScroll:true});
    }
    if (categorySwitch.hasPointerCapture(event.pointerId)) categorySwitch.releasePointerCapture(event.pointerId);
  }
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => categorySwitch.addEventListener(type, finishDrag));
  categorySwitch.addEventListener('click', event => {
    if (performance.now() < suppressClickUntil) {event.preventDefault(); event.stopImmediatePropagation();}
  }, true);
  tabs.forEach((tab,index) => {
    tab.addEventListener("click",() => setCategory(tab.dataset.category));
    tab.addEventListener("keydown",event => {
      if (busy() || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      setCategory(tabs[target].dataset.category);tabs[target].focus();
    });
  });
  status("正在连接设备服务。","正在连接任务队列。","working");
  render();
  async function loop() {await sync();setTimeout(loop,config.pollIntervalMs);}
  loop();
})();
