// 设备状态和 demo/real 模式由服务器决定，不要在这里存放设备密钥。
window.REACH_CONFIG = Object.freeze({
  apiBase: "", statusPath: "/api/status", queuePath: "/api/queue", grabPath: "/api/orders",
  taskPath: "/api/orders/{taskId}",
  // 新增 Runner 物品任务后，在这里把商品对应值改成其注册的 item_id。
  itemTasks: {water:'water', cola:'cola', oolong_tea:'oolong_tea',
    potato_chips:'water', cookies:'water', chocolate:'water',
    americano:'water', latte:'water', cappuccino:'water'},
  // type: "image"（图片/MJPEG）或 "video"（浏览器可播放的视频）。
  // 接入真实相机后设置 demo: false；WebRTC/RTSP 需要另行适配。
  cameras: {
    head: {type:"image", src:"assets/camera-head.svg", demo:true},
    left: {type:"snapshot", src:"/api/cameras/left/frame.jpg", demo:false, refreshMs:33},
    right: {type:"image", src:"assets/camera-right.svg", demo:true}
  },
  requestTimeoutMs: 8000, pollIntervalMs: 1000
});
