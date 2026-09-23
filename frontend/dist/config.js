// 设备状态和 demo/real 模式由服务器决定，不要在这里存放设备密钥。
window.REACH_CONFIG = Object.freeze({
  apiBase: "", statusPath: "/api/status", queuePath: "/api/queue", grabPath: "/api/orders",
  taskPath: "/api/orders/{taskId}",
  // 新增 Runner 物品任务后，在这里把商品对应值改成其注册的 item_id。
  itemTasks: {water:'water', cola:'cola', oolong_tea:'oolong_tea',
    potato_chips:'water', cookies:'water', chocolate:'water',
    americano:'water', latte:'water', cappuccino:'water'},
  // type: "snapshot" 定时读取后端 JPEG；未接入的相机由页面显示示意图。
  cameras: {
    head: {type:"snapshot", src:"/api/cameras/head/frame.jpg", demo:false, refreshMs:200},
    left: {type:"snapshot", src:"/api/cameras/left/frame.jpg", demo:false, refreshMs:33},
    right: {type:"snapshot", src:"/api/cameras/right/frame.jpg", demo:false, refreshMs:200}
  },
  requestTimeoutMs: 8000, pollIntervalMs: 1000
});
