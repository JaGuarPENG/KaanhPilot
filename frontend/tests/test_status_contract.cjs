const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

// Exercise the actual response validator without starting a robot or browser.
const source = fs.readFileSync(`${__dirname}/../dist/app.js`, 'utf8');
const validator = source.slice(source.indexOf('  function accept(data) {'), source.indexOf('  function showTask(task) {'));
const originals = ['water', 'cola', 'oolong_tea', 'potato_chips', 'cookies', 'chocolate'];
const products = [...originals, 'americano', 'latte', 'cappuccino'];
function validate(items) {
  const context = vm.createContext({
    cards: products.map(id => ({
      dataset: {item:id},
      closest: () => ({dataset:{category:originals.includes(id) ? 'drinks' : 'coffee'}})
    })),
    state:null, online:false,
    data:{revision:1, mode:'real', dry_run:true, ready:true, active_task:null, items}
  });
  vm.runInContext(`${validator}\naccept(data);`, context);
  return context;
}
const inventory = () => Object.fromEntries(products.map(id => [id, {name:id, available:1}]));
test('complete nine-product gateway enables coffee inventory', () => {
  const result = validate(inventory());
  assert.equal(result.online, true);
  assert.equal(result.state.items.water.available, 1);
  assert.equal(result.state.items.americano.available, 1);
});
test('missing coffee inventory rejects incomplete gateway response', () => {
  const items = inventory(); delete items.americano;
  assert.throws(() => validate(items), /设备状态接口格式不正确/);
});
test('missing original inventory still rejects the response', () => {
  const items = inventory(); delete items.water;
  assert.throws(() => validate(items), /设备状态接口格式不正确/);
});
test('registered coffee inventory must still be valid', () => {
  assert.throws(() => validate({...inventory(), americano:{name:'美式', available:2}}), /设备状态接口格式不正确/);
  assert.equal(validate({...inventory(), americano:{name:'美式', available:1}}).online, true);
});
