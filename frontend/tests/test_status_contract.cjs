const assert = require('node:assert/strict');
const test = require('node:test');
const model = require('../dist/queue-model.js');
const fs = require('node:fs');
const vm = require('node:vm');
test('late poll cannot regress a newer order snapshot', () => {
  const source = fs.readFileSync(`${__dirname}/../dist/app.js`, 'utf8');
  const remember = source.slice(source.indexOf('  function remember(order) {'), source.indexOf('  function refreshItems() {'));
  const context = vm.createContext({model, orders:new Map(), aliases:new Map(), stock:{}, terminal:new Set(['succeeded','failed','cancelled'])});
  vm.runInContext(remember + `
    remember({order_id:'1',item_id:'water',status:'failed',error_code:'out_of_stock',updated_at:20});
    remember({order_id:'1',item_id:'water',status:'running',updated_at:10});`, context);
  assert.equal(context.orders.get('1').status, 'failed');
  assert.equal(context.stock.water, 0);
});
test('accepts raw Runner status and queue, rejects broken contracts', () => {
  assert.equal(model.validRunner({state:'idle', accepting_orders:true}, {capacity:10,current_order:null,pending_orders:[]}), true);
  assert.equal(model.validRunner({accepting_orders:true}, {pending_orders:[]}), false);
});
test('only queued and paused orders can be cancelled', () => {
  assert.equal(model.canCancel({status:'queued'}), true);
  assert.equal(model.canCancel({status:'paused'}), true);
  assert.equal(model.canCancel({status:'running'}), false);
});
test('stock updates only for explicit stock failure, applies to shared task aliases', () => {
  const stock = {};
  model.updateStock(stock, {item_id:'water', status:'paused', error_code:null});
  assert.equal(stock.water, undefined);
  model.updateStock(stock, {item_id:'water', status:'failed', error_code:'out_of_stock'});
  assert.equal(stock.water, 0);
  const items = model.items([{id:'latte',name:'拿铁'}], {latte:'water'}, stock);
  assert.equal(items.latte.available, 0);
  model.updateStock(stock, {item_id:'water', status:'succeeded',error_code:null});
  assert.equal(stock.water, 1);
});
