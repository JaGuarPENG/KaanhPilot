// 设备状态和 demo/real 模式由服务器决定，不要在这里存放设备密钥。
window.REACH_CONFIG = Object.freeze({
  apiBase: "", statusPath: "/api/health", grabPath: "/api/grab",
  taskPath: "/api/tasks/{taskId}", stopPath: "/api/stop",
  // type: "image"（图片/MJPEG）或 "video"（浏览器可播放的视频）。
  // 接入真实相机后设置 demo: false；WebRTC/RTSP 需要另行适配。
  cameras: {
    head: {type:"snapshot", src:"/api/cameras/head/frame.jpg", demo:false, refreshMs:33},
    left: {type:"image", src:"assets/camera-left.svg", demo:true},
    right: {type:"image", src:"assets/camera-right.svg", demo:true}
  },
  requestTimeoutMs: 8000, pollIntervalMs: 1000
});
