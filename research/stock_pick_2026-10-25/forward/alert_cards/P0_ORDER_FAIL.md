# P0 下单失败/部分发送/持仓不一致

- 通知：电话+邮件+webhook｜响应：15 分钟

现象：下单返回非成功、循环异常、服务端持仓与本地预期不一致
字段：decision / n_real_orders / order_id_mask / positions_after / error_type
持仓快照命令：`cd /opt/quant && set -a; . /opt/quant/env; set +a; /opt/quant/.venv-gm/bin/python scripts/probe_automation.py snapshot`
撤单命令（人工确认后）：`cd /opt/quant && set -a; . /opt/quant/env; set +a; /opt/quant/.venv-gm/bin/python scripts/probe_automation.py snapshot --cancel-cl-ord-id '<从快照复制 cl_ord_id>'`
联系电话：[HUMAN-FILL: 本人填写]；第二人：[HUMAN-FILL: 本人填写]
15 分钟无确认 ⇒ 自动 disable 全部 deepquant-sim-*.timer，等待人工
