const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const model = require('../dist/queue-model.js');
const source = fs.readFileSync(`${__dirname}/../dist/app.js`, 'utf8');
const syncSource = source.slice(source.indexOf('  async function sync() {'), source.indexOf('  async function grab(id) {'));
const order = (id, status) => ({order_id:id,item_id:'water',status});
function setup(current, pending, tracked) {
  const orders = new Map([current, ...pending, tracked].filter(Boolean).map(value => [value.order_id,value]));
  const context = vm.createContext({model, orders, aliases:new Map(), stock:{},
    terminal:new Set(['succeeded','failed','cancelled']), syncing:null, writing:false, uncertain:false,
    runtimeId:'runtime', trackedTask:tracked?.order_id, selectedOrder:tracked, state:null, online:false,
    config:{statusPath:'/status',queuePath:'/queue'},
    responseQueue:{current_order:current,pending_orders:pending,capacity:10},
    remember:value => (orders.set(value.order_id,value),value),
    refreshItems(){}, render(){}, status(){}, step(){}
  });
  vm.runInContext(`
    async function api(path) {
      if(path === '/status') return {state:'running',accepting_orders:true};
      if(path === '/queue') return responseQueue;
      return {dry_run:true,runtime_id:'runtime'};
    }
    function showTask(value) {selectedOrder=value;trackedTask=value.order_id;}
    ${syncSource}`,context);
  return context;
}
test('poll prefers executing order over last submitted order', async () => {
  const current=order('first','running'), last=order('second','queued');
  const context=setup(current,[last],last);
  await vm.runInContext('sync()',context);
  assert.equal(context.selectedOrder.order_id,'first');
});
test('completion automatically switches progress to the next order', async () => {
  const previous=order('first','succeeded'), current=order('second','running');
  const context=setup(current,[],previous);
  await vm.runInContext('sync()',context);
  assert.equal(context.selectedOrder.order_id,'second');
});
test('empty queue retains last completed result', async () => {
  const previous=order('first','succeeded');
  const context=setup(null,[],previous);
  await vm.runInContext('sync()',context);
  assert.equal(context.selectedOrder,previous);
});
test('submission does not replace the current progress', async () => {
  const current=order('first','running'), added=order('second','queued');
  const context=vm.createContext({busy:()=>false,online:true,model:{canSubmit:()=>true},
    state:{},config:{grabPath:'/orders',itemTasks:{water:'water'}},writing:false,browsingProducts:false,
    aliases:new Map(),remember:value=>value,persistPending(){},render(){},status(){},sync:async()=>{},
    api:async()=>added,selectedOrder:current,showTask(value){context.selectedOrder=value;}});
  const grab=source.slice(source.indexOf('  async function grab(id) {'),source.indexOf('  async function cancelOrder(id) {'));
  vm.runInContext(grab,context);
  await vm.runInContext("grab('water')",context);
  assert.equal(context.selectedOrder,current);
});
