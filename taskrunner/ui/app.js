"use strict";

const labels = {
  items: { water: "矿泉水", cola: "可乐", oolong_tea: "乌龙茶" },
  runner: { not_started: "未启动", idle: "空闲", running: "运行中", faulted: "故障停止", stopped: "已停止" },
  status: {
    blocked: "等待前序", queued: "排队中", running: "执行中", paused: "等待处理",
    cancelling: "取消中", succeeded: "已成功", failed: "失败", cancelled: "已取消", skipped: "已跳过"
  },
  tasks: {
    pick: "抓取识别", transport_to_dropoff: "运送到放置位", place: "放置", return_and_reset: "返回并复位"
  },
  pauses: { second_detection_failed: "第二次识别失败", target_unreachable: "目标点不可达" }
};

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const knownOrderIds = new Set();
let pollInFlight = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  let body;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) throw new Error(body?.error?.message || `请求失败 (${response.status})`);
  return body;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statusBadge(status) {
  return el("span", `status ${status || "neutral"}`, labels.status[status] || status || "未知");
}

function taskChain(tasks) {
  const chain = el("div", "task-chain");
  tasks.forEach((task, index) => {
    const card = el("div", "task");
    card.append(el("span", "task-index", `0${index + 1}`));
    card.append(el("strong", "task-name", labels.tasks[task.task_type] || task.task_type));
    card.append(statusBadge(task.status));
    chain.append(card);
  });
  return chain;
}

function cancelButton(order) {
  const allowed = order.status === "queued" || order.status === "paused";
  const button = el("button", "cancel-button", allowed ? "取消订单" : "当前不可取消");
  button.disabled = !allowed;
  if (allowed) button.addEventListener("click", () => cancelOrder(order.order_id));
  return button;
}

function renderCurrent(order) {
  const root = document.querySelector("#current-order");
  const badge = document.querySelector("#current-status");
  root.replaceChildren();
  badge.className = "status neutral";
  if (!order) {
    badge.textContent = "空闲";
    root.className = "empty-state";
    const icon = el("div", "empty-icon", "✓");
    root.append(icon, el("p", "", "没有正在执行的订单"));
    return;
  }
  root.className = "order-summary";
  badge.className = `status ${order.status}`;
  badge.textContent = labels.status[order.status] || order.status;
  const row = el("div", "summary-row");
  const details = el("div");
  details.append(el("div", "item-name", labels.items[order.item_id] || order.item_id));
  details.append(el("div", "order-id", order.order_id));
  row.append(details, cancelButton(order));
  root.append(row);
  const reason = order.error_code ? labels.pauses[order.error_code] || order.error_code : "";
  root.append(el("p", "order-message", order.message || reason));
  root.append(taskChain(order.tasks));
}

function renderPending(orders) {
  const root = document.querySelector("#pending-orders");
  root.replaceChildren();
  if (!orders.length) {
    root.append(el("p", "empty-copy", "等待队列为空"));
    return;
  }
  orders.forEach((order, index) => {
    const card = el("div", "queue-order");
    const top = el("div", "queue-order-top");
    const title = el("div");
    title.append(el("span", "queue-position", `#${index + 1} `));
    title.append(document.createTextNode(labels.items[order.item_id] || order.item_id));
    top.append(title, cancelButton(order));
    card.append(top, el("div", "order-id", order.order_id));
    const mini = el("div", "mini-chain");
    order.tasks.forEach(task => mini.append(el("span", `mini-task ${task.status}`)));
    card.append(mini);
    root.append(card);
  });
}

function renderStatus(status) {
  const text = document.querySelector("#runner-state");
  const dot = document.querySelector("#connection-dot");
  const fault = document.querySelector("#fault-panel");
  text.textContent = labels.runner[status.state] || status.state;
  dot.className = `dot${status.state === "faulted" ? " faulted" : ""}`;
  document.querySelectorAll(".drink").forEach(button => { button.disabled = !status.accepting_orders; });
  if (status.state === "faulted") {
    fault.classList.remove("hidden");
    document.querySelector("#fault-message").textContent =
      `${status.fatal_error_type || "Error"}: ${status.fatal_error_message || "未知故障"}`;
  } else {
    fault.classList.add("hidden");
  }
}

function showToast(title, message, isError = false) {
  const toast = el("div", `toast${isError ? " error" : ""}`);
  toast.append(el("strong", "", title), el("span", "", message || ""));
  document.querySelector("#toasts").append(toast);
  window.setTimeout(() => toast.remove(), 8000);
}

async function reportFinished(orderId) {
  try {
    const order = await api(`/api/orders/${encodeURIComponent(orderId)}`);
    if (terminal.has(order.status)) {
      showToast(
        `${labels.items[order.item_id] || order.item_id} · ${labels.status[order.status] || order.status}`,
        order.message || order.error_code || "订单已结束",
        order.status !== "succeeded"
      );
    }
  } catch (error) {
    showToast("无法读取订单最终状态", error.message, true);
  }
}

async function refresh() {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const [status, queue] = await Promise.all([api("/api/status"), api("/api/queue")]);
    renderStatus(status);
    renderCurrent(queue.current_order);
    renderPending(queue.pending_orders);
    document.querySelector("#pending-count").textContent = String(queue.pending_orders.length);
    document.querySelector("#queue-capacity").textContent = String(queue.capacity);

    const active = new Set(queue.pending_orders.map(order => order.order_id));
    if (queue.current_order) active.add(queue.current_order.order_id);
    for (const orderId of knownOrderIds) {
      if (!active.has(orderId)) {
        knownOrderIds.delete(orderId);
        void reportFinished(orderId);
      }
    }
    active.forEach(orderId => knownOrderIds.add(orderId));
  } catch (error) {
    document.querySelector("#runner-state").textContent = "连接中断";
    document.querySelector("#connection-dot").className = "dot offline";
  } finally {
    pollInFlight = false;
  }
}

async function submitOrder(itemId) {
  try {
    const order = await api("/api/orders", { method: "POST", body: JSON.stringify({ item_id: itemId }) });
    knownOrderIds.add(order.order_id);
    showToast("订单已提交", `${labels.items[itemId] || itemId} · ${order.order_id}`);
    await refresh();
  } catch (error) {
    showToast("提交失败", error.message, true);
  }
}

async function cancelOrder(orderId) {
  try {
    const order = await api(`/api/orders/${encodeURIComponent(orderId)}/cancel`, { method: "POST", body: "{}" });
    if (terminal.has(order.status)) {
      knownOrderIds.delete(orderId);
      showToast("订单已取消", order.message || orderId, true);
    }
    await refresh();
  } catch (error) {
    showToast("取消失败", error.message, true);
  }
}

document.querySelectorAll(".drink").forEach(button => {
  button.addEventListener("click", () => submitOrder(button.dataset.item));
});

void refresh();
window.setInterval(refresh, 500);
