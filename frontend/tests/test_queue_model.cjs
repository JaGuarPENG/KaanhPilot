const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modulePath = path.join(__dirname, '../dist/queue-model.js');
test('four stages reflect task status, not just order status', () => {
  assert.ok(fs.existsSync(modulePath), 'queue model is not implemented');
  const model = require(modulePath);
  const order = {status:'running', tasks:[
    {task_type:'pick',status:'succeeded'},
    {task_type:'transport_to_dropoff',status:'running'},
    {task_type:'place',status:'blocked'},
    {task_type:'return_and_reset',status:'blocked'}]};
  assert.deepEqual(model.stages(order).map(x => x.status), ['succeeded','running','blocked','blocked']);
  assert.equal(model.progress(order), 25);
  assert.equal(model.canSubmit({accepting_orders:true, items:{water:{available:null}}}, 'water'), true);
  assert.equal(model.canSubmit({accepting_orders:true, items:{water:{available:0}}}, 'water'), false);
});
test('paused cancelled and skipped stages never look completed', () => {
  assert.ok(fs.existsSync(modulePath), 'queue model is not implemented');
  const model = require(modulePath);
  const order = {status:'cancelled', tasks:[{task_type:'pick',status:'cancelled'},
    ...['transport_to_dropoff','place','return_and_reset'].map(task_type=>({task_type,status:'skipped'}))]};
  assert.equal(model.progress(order), 0);
  assert.equal(model.stages(order)[3].label, '返回复位');
  assert.equal(model.statusLabel('paused'), '等待处理');
});
