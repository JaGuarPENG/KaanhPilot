"use strict";
(() => {
  const config = window.REACH_CONFIG;
  const cards = [...document.querySelectorAll(".drink-card")];
  const byId = id => document.getElementById(id);
  const stop = byId("stop-button");
  let state = null, online = false, writing = false, uncertain = false;
  let trackedTask = null, syncing = null;
  let testingStock = false;
  let activePage = "shop";
  const pageScroll = {shop:0, orders:0};
  const dialog = byId('maintenance-dialog');
  function setPage(page) {
    if (!['shop','orders'].includes(page) || page === activePage) return;
    pageScroll[activePage] = window.scrollY;
    activePage = page;
    byId('shop-page').hidden = page !== 'shop';
    byId('order-page').hidden = page !== 'orders';
    byId('order-summary').hidden = page !== 'shop';
    document.querySelectorAll('[data-page]').forEach(button => {
      if (button.dataset.page === page) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    window.scrollTo({top:pageScroll[page], behavior:'instant'});
  }
  document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => setPage(button.dataset.page)));
  byId('view-order').addEventListener('click', () => {
    setPage('orders');
    byId('order-heading').setAttribute('tabindex', '-1');
    byId('order-heading').focus({preventScroll:true});
  });
  byId('maintenance-toggle').addEventListener('click', () => dialog.showModal());
  byId('maintenance-close').addEventListener('click', () => dialog.close());
  // Reserve the measured dock height even with larger text or a device safe area.
  const dock = document.querySelector('.bottom-dock');
  new ResizeObserver(() => document.documentElement.style.setProperty('--dock-height', dock.getBoundingClientRect().height + 'px')).observe(dock);
  // 每个画面单独配置；接入图片/MJPEG 或视频时仅替换 config.js 中的源地址。
  document.querySelectorAll("[data-camera]").forEach(frame => {
    const name = frame.dataset.camera;
    const source = config.cameras?.[name];
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
        const available = state?.cameras?.[name] === true;
        if (state && !available) {
          if (objectUrl) {URL.revokeObjectURL(objectUrl); objectUrl = null;}
          if (!media.getAttribute('src')?.startsWith('assets/')) media.src = `assets/camera-${name}.svg`;
          media.alt = '相机示意画面';
          frame.querySelector('.camera-error').hidden = true;
          frame.querySelector('.placeholder-note').textContent = '示意画面 · 非实时';
        }
        if (!online && available) frame.querySelector('.camera-error').hidden = false;
        if (activePage === "orders" && !document.hidden && online && available) {
          frame.querySelector('.placeholder-note').textContent = '相机画面';
          media.alt = ({head:'头部', left:'左手', right:'右手'}[frame.dataset.camera]) + '相机画面';
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
      frame.querySelector(".camera-error").hidden = true;
      refresh();
    } else {
      media.src = source.src;
    }
    frame.querySelector(".placeholder-note").textContent = source.type === "snapshot" ? "示意画面 · 非实时" : source.demo === false ? "相机画面" : "示意画面 · 非实时";
  });
  function updateCameraLabels() {
    const names = {head:'头部', left:'左手', right:'右手'};
    const live = Object.keys(names).filter(key => {
      const source = config.cameras?.[key];
      return source?.demo === false && (source.type !== 'snapshot' || state?.cameras?.[key] === true);
    });
    document.querySelector('.demo-label').lastChild.textContent = live.length ? live.map(key => names[key]).join('、') + '相机' : '示意画面';
    document.querySelector('.camera-footnote p span').textContent = live.length === 3 ? '三个视角均为实时画面。' : live.length ? '未接入的视角显示示意画面。' : '当前为示意画面，非实时影像。';
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
    byId('summary-title').textContent = title;
    byId('summary-detail').textContent = detail;
    const mappedItem = selectedOrder && selectedOrder.item_id !== selectedOrder.execution_item_id;
    byId('summary-detail').hidden = kind !== 'error' && Boolean(selectedOrder) && !mappedItem;
    byId('order-summary').dataset.kind = kind;
    const symbol = byId('status-symbol');
    symbol.className = 'status-symbol ' + kind;
    symbol.textContent = kind === 'success' ? '✓' : kind === 'error' ? '!' : '';
  }
  function step(order) {
    byId('task-progress').hidden = !order;
    byId('summary-progress').hidden = !order;
    const stages = model.stages(order);
    document.querySelectorAll('[data-step]').forEach((el, i) => {
      const stage = stages[i];
      el.dataset.status = stage.status;
      el.replaceChildren(document.createTextNode(stage.label));
      const detail = document.createElement('small');
      detail.textContent = model.statusLabel(stage.status);
      el.append(detail);
    });
    byId('summary-progress').replaceChildren(...[...document.querySelectorAll('[data-step]')].map(el => {const clone = el.cloneNode(true); clone.removeAttribute('data-step'); return clone;}));
    byId('progress-fill').style.width = model.progress(order) + '%';
    byId('progress-fill').style.background = order?.status === 'failed' ? '#b54637' : '';
  }
  let detailOrderId = null;
  const listVersions = new Map();
  byId('detail-close').addEventListener('click', () => byId('order-detail-dialog').close());
  function renderOrderDetail() {
    const order = orders.get(detailOrderId);
    if (!order) {byId('order-detail-dialog').close(); return;}
    byId('detail-name').textContent = state.items[order.item_id]?.name || order.item_id;
    byId('detail-status').textContent = model.statusLabel(order.status) + ' · ' + order.order_id;
    const mapped = order.item_id !== order.execution_item_id ? `当前实际执行${state.items[order.execution_item_id]?.name || order.execution_item_id}任务。` : '';
    byId('detail-message').textContent = mapped + (order.message || '');
    byId('detail-progress').replaceChildren(...model.stages(order).map(stage => {
      const item = document.createElement('li'); item.dataset.status = stage.status;
      item.textContent = stage.label;
      const status = document.createElement('small'); status.textContent = model.statusLabel(stage.status);
      item.append(status); return item;
    }));
  }
  function updateOrderList(id, values, history) {
    const version = JSON.stringify([values, writing, online]);
    if (listVersions.get(id) === version) return;
    listVersions.set(id, version);
    const list = byId(id);
    const focusRow = document.activeElement?.closest('.queue-row');
    const focusId = focusRow?.dataset.orderId;
    const focusClass = document.activeElement?.className;
    const scrollTop = list.scrollTop;
    list.replaceChildren(...values.map((order, i) => orderRow(order, i, history)));
    list.scrollTop = scrollTop;
    if (focusId && focusRow.parentElement === null) {
      const replacement = [...list.children].find(row => row.dataset.orderId === focusId);
      replacement?.querySelector(focusClass === 'queue-cancel' ? '.queue-cancel' : '.queue-select')?.focus({preventScroll:true});
    }
  }
  function orderRow(order, index, history = false) {
    const row = document.createElement('li');
    row.className = 'queue-row';
    row.dataset.orderId = order.order_id;
    const select = document.createElement('button');
    select.className = 'queue-select';
    const name = state.items[order.item_id]?.name || order.item_id;
    select.textContent = (history ? '' : index === 0 && order.status !== 'queued' ? '当前 · ' : '等待 · ') + name;
    const detail = document.createElement('small');
    detail.textContent = model.statusLabel(order.status) + ' · ' + order.order_id.slice(0, 8);
    select.append(detail);
    select.addEventListener('click', () => {detailOrderId = order.order_id; renderOrderDetail(); byId('order-detail-dialog').showModal();});
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
    const orders = queue.pending_orders;
    byId('queue-panel').hidden = false;
    byId('queue-count').textContent = `等待 ${queue.pending_count} / ${queue.capacity}`;
    byId('queue-preview').textContent = orders.length ? orders.map(order => state.items[order.item_id]?.name || order.item_id).join('、') : '暂无等待订单';
    updateOrderList('queue-list', orders, false);
    const note = byId('queue-note');
    note.textContent = state.fatal_error ? '设备故障：' + state.fatal_error :
      queue.current_order?.status === 'paused' ? '当前订单等待处理，后续订单暂不执行。' : '';
    note.hidden = !note.textContent;
    updateOrderList('recent-orders', state.recent_orders.filter(order => terminal.has(order.status)).slice(0, 10), true);
    if (byId('order-detail-dialog').open) renderOrderDetail();
  }
  function render() {
    const locked = busy();
    refreshItems();

    cards.forEach(card => {
      const item = state?.items[card.dataset.item];
      const soldOut = item?.available === 0;
      card.disabled = locked || !online || !model.canSubmit(state, card.dataset.item);
      card.classList.toggle('sold-out', soldOut);
      card.setAttribute('aria-label', (item?.name || card.querySelector('.drink-name').textContent) + (soldOut ? '，已售尽' : '，加入队列'));
      const selected = state?.queue.current_order?.item_id === card.dataset.item;
      card.classList.toggle('selected', selected); card.setAttribute('aria-pressed', String(selected));
      card.querySelector('.card-action').textContent = soldOut ? '已售尽' : '加入队列';
    });
    byId('inventory-test').hidden = !online || state?.inventory_test !== true;
    const occupied = Boolean(state?.queue.current_order || state?.queue.pending_count);
    byId('inventory-test-form').querySelectorAll('select, button').forEach(el => {el.disabled = locked || occupied || !online;});
    byId('restock-panel').hidden = !online;
    byId('uncertain-panel').hidden = !uncertain;
    byId('uncertain-confirm').disabled = writing || !online;
    byId('restock-form').querySelectorAll('select, button').forEach(el => {el.disabled = locked || occupied || !online;});

    stop.disabled = writing || !online || !selectedOrder?.allowed_actions?.includes('cancel');
    stop.textContent = selectedOrder?.status === 'paused' ? '取消并回位' : '取消订单';
    byId('connection-label').textContent = !online ? '连接中断' : state.fatal_error ? '设备故障' : state.dry_run ? '模拟控制器' : state.accepting_orders ? '可以下单' : '暂不接单';
    byId('connection').className = 'connection ' + (online && !state?.fatal_error ? 'online' : 'error');
    renderQueue();
    byId('status-symbol').hidden = Boolean(selectedOrder);
    updateCameraLabels();
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
    const product = cards.find(card => card.dataset.item === task.item_id);
    byId('order-thumbnail').hidden = !product;
    if (product) byId('order-thumbnail').className = product.querySelector('.drink-image').className + ' order-thumbnail';
    byId('order-heading').textContent = terminal.has(task.status) ? '最近订单' : task.status === 'queued' ? '等待订单' : '当前订单';
    const name = state?.items[task.item_id]?.name || task.item_id;
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
          byId('order-thumbnail').hidden = true; byId('order-heading').textContent = '当前订单';
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
        state = {...runnerStatus, dry_run:info.dry_run, cameras:info.cameras,
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
    writing = true;
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
  status("正在连接设备服务。","正在连接任务队列。","working");
  render();
  async function loop() {await sync();setTimeout(loop,config.pollIntervalMs);}
  loop();
})();
