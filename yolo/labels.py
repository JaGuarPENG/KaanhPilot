"""
模型类别名到项目目标标识的映射加载与校验。

修改标签映射时请注意：
- 修改模型中的yaml来增加删减类别
- 只检查 yaml 配置的类别是否存在于模型中；如果模型额外包含一个未配置类别，当前实现不会报错
- yaml左侧必须与 .pt checkpoint 内的类别名称完全一致；右侧为项目稳定目标标识
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class LabelMapping:
    """将模型内部类别名映射为稳定的项目 target_id。

    不以 class_id 作为配置键，因为重训或换模型后 class_id 的顺序可能改变；
    类别名称才是可读、可审查的模型契约。
    """

    model_label_to_target: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.model_label_to_target:
            raise ValueError("标签映射不能为空")
        if any(not label or not target for label, target in self.model_label_to_target.items()):
            raise ValueError("模型类别名和 target_id 都不能为空")
        if len(set(self.model_label_to_target.values())) != len(self.model_label_to_target):
            raise ValueError("一个 target_id 不能映射到多个模型类别名")

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(self.model_label_to_target.values())

    def class_id_for_target(self, model_names: Mapping[int, str], target_id: str) -> int:
        """根据当前 checkpoint 实际类别表解析目标的 class_id。"""
        configured_label = None
        for label, target in self.model_label_to_target.items():
            if target == target_id:
                configured_label = label
                break
        if configured_label is None:
            raise ValueError(f"目标 {target_id!r} 未在标签映射中配置")
        for class_id, model_label in model_names.items():
            if model_label == configured_label:
                return int(class_id)
        raise ValueError(f"模型不包含映射要求的类别 {configured_label!r}")

    def validate_model_names(self, model_names: Mapping[int, str]) -> None:
        """启动时校验配置中的每一类都确实存在于当前模型，避免静默错配。"""
        available = set(model_names.values())
        missing = sorted(set(self.model_label_to_target) - available)
        if missing:
            raise ValueError(f"模型缺少标签映射中的类别: {', '.join(missing)}")


def load_label_mapping(path: Path) -> LabelMapping:
    """从 YAML 文件读取 ``model_labels: {模型类名: target_id}``。"""
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("读取 YOLO 标签映射需要安装 PyYAML") from error

    with path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict) or not isinstance(document.get("model_labels"), dict):
        raise ValueError(f"标签映射文件格式无效: {path}")
    return LabelMapping({str(label): str(target) for label, target in document["model_labels"].items()})
