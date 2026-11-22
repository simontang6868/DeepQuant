# P2 自检警告（NTP/磁盘/陈旧文件）

- 通知：邮件｜响应：次日

现象：NTP 未同步、磁盘余量低、输入文件陈旧
字段：warn / available_at_check / ledger_mtime
无需立即撤单；下一交易日复跑前置自检
