"""配置冻结与**运行时**篡改检测。

为什么不能只在加载时比对
----------------------
"读的时候哈希是对的"证明不了"用的时候还是对的"。会话跑了 4 小时，中间有人
把单笔上限从 4000 改成 40000，加载时那次校验早就过去了。所以这里的设计是：

**每一次取值都重算文件哈希**（``FrozenConfig.get`` 内部调 ``verify``）。
代价是每次几微秒的读文件——相对于一笔委托的往返，可以忽略；而它换来的是
"配置在被使用的那一刻确实是冻结的那份"。

威胁模型（明确边界，免得高估它）
------------------------------
- **能防**：会话运行期间配置文件被改/被替换/被删除。
- **不能防**：进程内存被改、代码被改、攻击者同时改文件和 ``FrozenConfig`` 里的哈希。
  那些属于操作系统与构建链路层面，不是这个类能解决的。

⚠️ 一条刻意的保守：**内容不变但重新格式化也会报篡改**。对"冻结"而言格式变化
也是变化；放宽它等于开一个"重排一遍再顺手改个数值"的口子。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["ConfigTampered", "FrozenConfig", "freeze_config"]


class ConfigTampered(RuntimeError):
    """配置在冻结之后被改动（含删除、格式变化、内容变化）。"""


_MISSING = object()


def _sha256_file(path: pathlib.Path) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigTampered(f"配置文件不见了：{path}") from exc
    except OSError as exc:
        raise ConfigTampered(f"配置文件读不了：{path}（{exc}）") from exc
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FrozenConfig:
    """冻结后的配置。只能通过 ``freeze`` 构造。"""

    path: pathlib.Path
    sha256: str
    data: Mapping[str, Any]
    #: 每次成功校验的逻辑时间戳/标签。用于事后回答"什么时候校过"。
    checks: list[str] = field(default_factory=list, compare=False, repr=False)

    # ---------------------------------------------------------------- 构造
    @classmethod
    def freeze(cls, path: str | pathlib.Path) -> "FrozenConfig":
        p = pathlib.Path(path)
        sha = _sha256_file(p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigTampered(f"{p} 不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ConfigTampered(f"{p} 的顶层必须是对象，收到 {type(data).__name__}")
        return cls(path=p, sha256=sha, data=data)

    # ---------------------------------------------------------------- 校验
    def verify(self, *, tag: str = "") -> None:
        """重算文件哈希并比对。不一致即抛。"""
        actual = _sha256_file(self.path)
        if actual != self.sha256:
            raise ConfigTampered(
                f"{self.path.name} 在冻结后被改动："
                f"{self.sha256[:12]} → {actual[:12]}。"
                "配置不再是冻结的那份，拒绝继续使用")
        self.checks.append(tag or f"verify#{len(self.checks) + 1}")

    # ---------------------------------------------------------------- 取值
    def get(self, key: str, default: Any = _MISSING) -> Any:
        """取值。**先 verify**——这就是"运行时校验"。"""
        self.verify(tag=f"get:{key}")
        if key in self.data:
            return self.data[key]
        if default is _MISSING:
            raise KeyError(f"{self.path.name} 里没有 {key!r}")
        return default

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def snapshot(self) -> dict[str, Any]:
        """冻结时的内容快照（不再读文件）。用于与 ``verify`` 配合定位差异。"""
        return dict(self.data)


def freeze_config(path: str | pathlib.Path) -> FrozenConfig:
    """便利函数。"""
    return FrozenConfig.freeze(path)
