(function (root) {
  const types = ['pick', 'transport_to_dropoff', 'place', 'return_and_reset'];
  const labels = ['抓取', '运输', '放置', '返回复位'];
  const names = {blocked:'待执行', queued:'排队中', running:'进行中', paused:'等待处理',
    cancelling:'回位中', succeeded:'已完成', failed:'失败', cancelled:'已取消', skipped:'未执行', needs_attention:'待核对'};
  const model = {
    validRunner: (status, queue) => Boolean(status && ['not_started','idle','running','faulted','stopped'].includes(status.state)
      && typeof status.accepting_orders === 'boolean' && Number.isInteger(queue?.capacity)
      && Array.isArray(queue.pending_orders) && (queue.current_order === null || typeof queue.current_order?.order_id === 'string')),
    canCancel: order => ['queued','paused'].includes(order?.status),
    updateStock: (stock, order) => {
      if (order.status === 'failed' && order.error_code === 'out_of_stock') stock[order.item_id] = 0;
      else if (order.status === 'succeeded') stock[order.item_id] = 1;
    },
    items: (products, mapping, stock) => Object.fromEntries(products.map(item => [item.id,
      {name:item.name, available:stock[mapping[item.id]] ?? null}])),
    stages: order => types.map((type, index) => ({type, label:labels[index],
      status:order?.tasks?.find(task => task.task_type === type)?.status || 'blocked'})),
    progress: order => model.stages(order).filter(stage => stage.status === 'succeeded').length * 25,
    statusLabel: status => names[status] || status,
    canSubmit: (state, item) => Boolean(state?.accepting_orders && state.items[item] && state.items[item].available !== 0)
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = model;
  else root.QueueModel = model;
})(globalThis);
