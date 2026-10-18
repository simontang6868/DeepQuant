"""标的显示名称的规范化规则（写入端与读取端共用同一份）。

为什么单独一个模块
------------------
名称由导入流水线写进元数据（``ingest_daily_csv``），由 API 层读出来展示
（``api/datasets._parse_metadata``）。如果两边各写一套校验，就会出现
**写得进去、读不出来**：数据集在界面上直接变成「不可用」，而错误信息只会说
「元数据无效」，不指向真正的分歧点（是两个模块对同一条规则的实现不同）。

所以规则只有一份：本模块。写入端用它抛 ``ValueError``（调用方看到的是
「你给的名字不合法」），读取端用它在 ``except ValueError`` 里转成
「哪个字段为什么不可用」。

约束为什么是这些
----------------
- 必须是非空字符串：空串与 ``None`` 语义不同，空串一律当错误，避免「导入了但没名字」
  这种需要猜的状态；
- 允许首尾空白并在写盘前 ``strip()``：从 CSV/JSON 里抄名称带上空格是常态；
- 长度上限 64：A 股标的最长名称远小于此，设上限是为了拦住把整行/整段文本塞进名称；
- 禁止控制字符：名称要出现在下拉框与详情面板里，控制字符会破坏排版，
  也常是编码错乱的信号（宁可让导入失败，也不要显示一串乱码）。
"""

from __future__ import annotations

#: 显示名称的最大长度（字符数，非字节数）。
MAX_DISPLAY_NAME_LENGTH = 64


def normalize_display_name(value: object) -> str | None:
    """把任意输入规范化为可落盘的显示名称；``None`` 原样返回。

    参数：
        value: 待校验的名称。``None`` 表示「没有名称」，是合法输入。

    返回：
        去除首尾空白后的名称，或 ``None``。

    抛出：
        ValueError: 输入不是字符串、为空、过长或含控制字符。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("显示名称必须是字符串")

    text = value.strip()
    if not text:
        raise ValueError("显示名称不能为空")
    if len(text) > MAX_DISPLAY_NAME_LENGTH:
        raise ValueError(f"显示名称超过 {MAX_DISPLAY_NAME_LENGTH} 个字符")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError("显示名称不能包含控制字符")
    return text


__all__ = ["MAX_DISPLAY_NAME_LENGTH", "normalize_display_name"]
