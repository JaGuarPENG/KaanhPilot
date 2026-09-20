# KaanhPilot

## Python 环境

当前开发和真机验证环境：

- Windows
- Python 3.10
- Conda 环境名：`pyagent`

建议创建独立环境后安装项目依赖：

```powershell
conda create -n pyagent python=3.10
conda activate pyagent
python -m pip install -r requirements.txt
```

依赖版本来自当前已验证的 `pyagent` 环境。`pyorbbecsdk2` 用于 Orbbec G305，相机驱动及厂商运行库仍需按设备环境单独安装和确认。

项目结构、当前状态和后续计划见 [docs/README.md](./docs/README.md)。

首次克隆、分支开发、测试、Pull Request 和合并清理流程见 [开发规范](./CONTRIBUTING.md)。
