// Run with Playwright available in NODE_PATH; uses Edge and an isolated fixture API.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
let server, browser, origin;
const root = path.resolve(__dirname, '../dist');
before(async () => {
  server = http.createServer((req, res) => {
    const file = path.join(root, req.url.split('?')[0] === '/' ? 'index.html' : req.url.split('?')[0]);
    const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml'};
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.writeHead(404).end(); return;}
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
});
after(async () => {await browser?.close(); if (server) await new Promise(resolve => server.close(resolve));});
const order = (id, status = 'running') => ({order_id:id,item_id:'oolong_tea',status,
  current_task_type:'transport_to_dropoff',created_at:1,updated_at:1,message:'',error_code:null,
  tasks:['pick','transport_to_dropoff','place','return_and_reset'].map((task_type,i) =>
    ({task_type,status:i===0?'succeeded':i===1?'running':'blocked'}))});
async function setup(options = {}) {
  const page = await browser.newPage({viewport:options.viewport || {width:820,height:1230},hasTouch:true});
  page.setDefaultTimeout(5000);
  const errors=[]; page.on('pageerror', e=>errors.push(e.message));
  const data = {current:options.idle?null:order('current'),pending:[],posts:[],offline:false,
    cameras:options.cameras || {head:false,left:false,right:false},cameraRequests:[]};
  await page.route('**/api/**', async route => {
    if(data.offline) return route.abort();
    const req=route.request(), url=new URL(req.url()); let body;
    if(url.pathname==='/api/status') body={state:data.current?'running':'idle',accepting_orders:true,fatal_error_message:null};
    else if(url.pathname==='/api/info') body={dry_run:true,runtime_id:'fixture',inventory_test:true,
      camera_available:data.cameras.left,cameras:data.cameras};
    else if(url.pathname==='/api/queue') body={capacity:10,current_order:data.current,pending_orders:data.pending};
    else if(url.pathname.startsWith('/api/cameras/')) {
      data.cameraRequests.push(url.pathname);
      return route.fulfill({body:fs.readFileSync(path.join(root,'assets/snacks.png')),contentType:'image/jpeg'});
    }
    else if(url.pathname==='/api/orders' && req.method()==='POST') {
      data.posts.push(req.postDataJSON()); body={...order('queued-'+data.posts.length,'queued'),item_id:req.postDataJSON().item_id};
      data.pending.push(body);
    } else if(url.pathname.endsWith('/cancel')) {
      body={...data.current,status:'cancelled',updated_at:2}; data.current=null;
    } else if(url.pathname.startsWith('/api/orders/')) body={...order('current','succeeded'),updated_at:3};
    else body={ok:true};
    await route.fulfill({json:body});
  });
  if(options.uncertain) await page.addInitScript(()=>sessionStorage.setItem('kaanh-direct-pending', JSON.stringify({item_id:'water'})));
  await page.goto(origin);
  await page.waitForFunction(()=>document.querySelector('#connection').classList.contains('online'));
  return {page,data,errors};
}
test('all categories stay available and polling never takes over the ordering page', async () => {
  const {page,data,errors}=await setup();
  try {
    assert.equal(await page.getByRole('button',{name:'点单',exact:true}).count(),1);
    assert.equal(await page.locator('.drink-card:visible').count(),9);
    if (process.env.UI_SCREENSHOTS) {
      fs.mkdirSync(process.env.UI_SCREENSHOTS,{recursive:true});
      await page.setViewportSize({width:1024,height:1536});
      await page.screenshot({path:path.join(process.env.UI_SCREENSHOTS,'shop.png')});
      await page.setViewportSize({width:820,height:1230});
    }
    await page.locator('[data-item="latte"]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-item="latte"]').disabled);
    assert.deepEqual(data.posts,[{item_id:'water'}]);
    assert.equal(await page.locator('#order-page').isVisible(),false);
    assert.equal(await page.locator('.drink-card:visible').count(),9);
    await page.getByRole('button',{name:'订单',exact:true}).click();
    assert.equal(await page.locator('[data-camera]:visible').count(),3);
    assert.equal(await page.locator('.recent-orders').getAttribute('open'),null);
    assert.match(await page.locator('#status-title').textContent(),/乌龙茶/);
    if (process.env.UI_SCREENSHOTS) {
      await page.setViewportSize({width:1024,height:1536});
      await page.screenshot({path:path.join(process.env.UI_SCREENSHOTS,'orders.png')});
    }
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});
test('idle cameras and maintenance are reachable, paused cancellation keeps API semantics',async()=>{
  const {page,data,errors}=await setup({idle:true});
  try {
    await page.getByRole('button',{name:'订单',exact:true}).click();
    assert.equal(await page.locator('[data-camera]:visible').count(),3);
    await page.getByRole('button',{name:'维护',exact:true}).click();
    assert.equal(await page.locator('#restock-form').isVisible(),true);
    assert.equal(await page.locator('#inventory-test-form').isVisible(),true);
    await page.keyboard.press('Escape');
    data.current={...order('current','paused'),pause_reason:'second_detection_failed'};
    await page.waitForFunction(()=>document.querySelector('#stop-button').textContent==='取消并回位');
    await page.locator('#stop-button').click();
    await page.waitForFunction(()=>document.querySelector('#status-title').textContent.includes('已取消'));
    assert.equal(data.current,null);
    await page.locator('.recent-orders summary').click();
    assert.match(await page.locator('#recent-orders').textContent(),/乌龙茶/);
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});
test('camera cards follow availability independently and keep the missing view as a demo',async()=>{
  const {page,data,errors}=await setup({cameras:{head:true,left:false,right:false}});
  try {
    await page.getByRole('button',{name:'订单',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.demo-label').textContent.includes('头部相机'));
    await page.waitForFunction(()=>document.querySelector('.camera-head .camera-image').src.startsWith('blob:'));
    assert.ok(data.cameraRequests.some(path=>path.includes('/head/')));
    assert.equal(data.cameraRequests.some(path=>path.includes('/right/')),false);
    assert.match(await page.locator('.camera-right .placeholder-note').textContent(),/示意画面/);
    data.cameras={head:true,left:true,right:true};
    await page.waitForFunction(()=>document.querySelector('.camera-footnote').textContent.includes('三个视角均为实时画面'));
    await page.waitForFunction(()=>document.querySelector('.demo-label').textContent.includes('右手'));
    assert.deepEqual(errors,[]);
  } finally {await page.close();}
});
test('uncertain submissions are visible on entry and prevent duplicate orders',async()=>{
  const {page,data}=await setup({uncertain:true});
  try {
    assert.equal(await page.locator('#uncertain-panel').isVisible(),true);
    assert.ok((await page.locator('#uncertain-panel').boundingBox()).y < 300, 'recovery prompt is near the top');
    assert.equal(await page.locator('[data-item="water"]').isDisabled(),true);
    assert.equal(data.posts.length,0);
    await page.locator('#uncertain-confirm').click();
    await page.waitForFunction(()=>!document.querySelector('[data-item="water"]').disabled);
  } finally {await page.close();}
});
test('disconnection disables new orders and displays a persistent summary error',async()=>{
  const {page,data}=await setup();
  try {
    data.offline=true;
    await page.waitForFunction(()=>document.querySelector('#connection-label').textContent==='连接中断');
    assert.equal(await page.locator('[data-item="water"]').isDisabled(),true);
    assert.match(await page.locator('#summary-title').textContent(),/连接/);
    assert.equal(await page.locator('#order-summary').getAttribute('data-kind'),'error');
    data.offline=false;
    await page.waitForFunction(()=>!document.querySelector('[data-item="water"]').disabled);
  } finally {await page.close();}
});
test('portrait and narrow layouts keep two columns with the final product above the dock',async()=>{
  for(const viewport of [{width:820,height:1230},{width:768,height:1024},{width:390,height:844}]) {
    const {page}=await setup({viewport});
    try {
      const a=await page.locator('[data-item="water"]').boundingBox(),b=await page.locator('[data-item="cola"]').boundingBox();
      assert.ok(Math.abs(a.y-b.y)<2 && b.x>a.x,'two items per row');
      await page.locator('[data-item="cappuccino"]').scrollIntoViewIfNeeded();
      const last=await page.locator('[data-item="cappuccino"]').boundingBox(),dock=await page.locator('.bottom-dock').boundingBox();
      assert.ok(last.y+last.height<=dock.y+1,'last product is not covered');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    } finally {await page.close();}
  }
});
test('mapped-product execution remains explained on the shopping summary',async()=>{
  const {page,data}=await setup();
  try {
    await page.locator('[data-item="latte"]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-item="latte"]').disabled);
    data.current={...data.pending[0],status:'running',updated_at:2}; data.pending=[];
    await page.waitForFunction(()=>document.querySelector('#summary-title').textContent.includes('拿铁'));
    assert.equal(await page.locator('#summary-detail').isVisible(),true);
    assert.match(await page.locator('#summary-detail').textContent(),/实际执行矿泉水/);
    assert.equal(await page.locator('#summary-announcement').getAttribute('role'),'status');
  } finally {await page.close();}
});
test('order detail inspection and keyboard focus survive polling without replacing the current order',async()=>{
  const {page,data}=await setup();
  try {
    await page.locator('[data-item="latte"]').click();
    await page.waitForFunction(()=>!document.querySelector('[data-item="latte"]').disabled);
    await page.getByRole('button',{name:'订单',exact:true}).click();
    await page.locator('#queue-panel summary').click();
    const queued=page.locator('#queue-list .queue-select').filter({hasText:'拿铁'});
    await queued.focus();
    await page.waitForTimeout(1200);
    assert.equal(await queued.evaluate(el=>el===document.activeElement),true);
    await queued.click();
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('#order-detail-dialog').isVisible(),true);
    assert.match(await page.locator('#detail-name').textContent(),/拿铁/);
    assert.match(await page.locator('#status-title').textContent(),/乌龙茶/);
    await page.keyboard.press('Escape');
  } finally {await page.close();}
});
