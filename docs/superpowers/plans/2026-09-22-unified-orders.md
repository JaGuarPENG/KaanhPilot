# 单服务订单实现计划

当前分支内执行；用户已明确要求按讨论方案实现，不另建 worktree，不修改现有本机配置。

1. 新增服务行为测试，确认尚无实现时失败。实现 catalog、repository、inventory 和 OrderService；使用真实 TaskRunner 与可控假动作验证。
2. 新增统一 HTTP 测试并实现 services/server.py、模拟与硬件 runtime；保留硬件层代码。核对命名相机与退出生命周期。
3. 调整前端契约、四段 TaskStatus 进度、队列、取消、请求去重恢复和库存展示；保持现有卡片、相机和分类风格。新增 JS 行为测试。
4. 根启动入口接入统一服务，旧前端启动入口改为兼容转发；补充运行和商品扩展文档。
5. 执行服务、Runner、前端回归及可用的全项目测试，模拟浏览器验收；审阅变更，不连接真机。

## 执行记录

- 设计与范围根据已完成的会话方案确定。现有 config/launcher/launcher_config.json 与 config/robot/robot_config.json 有用户修改，保持不动。
- 新增 services 层，保留旧服务作为兼容代码；根 launcher 启动统一入口，旧前端快捷方式转发根 launcher。TaskRunner 和硬件层源码未修改。
- 已实现商品映射、幂等及重启核对、无库存别名同步、四阶段状态与队列、取消、登录及同源请求。
- 服务/HTTP/JS 行为先观察缺失实现导致的失败，再实现；独立审阅发现取消回位竞态和初始化中断清理，均补充失败回归后修复。
- 最终相关 Python 72 passed、2 skipped、7 subtests passed；JS 7 passed；语法检查、diff whitespace 检查通过。浏览器在模拟模式完成状态/排队/缺货/补货/窄屏检查。
- 全项目 pytest 收集被 7 项缺依赖或旧模块引用阻断，详情见 services/README.md。未运行真机。
