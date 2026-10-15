"""绩效分析、结果序列化与可复现性。

子模块（按需显式导入，避免与 backtest 产生循环依赖）：
- ``metrics``：绩效指标与交易统计（口径定义处）；
- ``serialize``：BacktestResult 的 JSON 往返序列化；
- ``reproducibility``：数据集摘要与策略元数据；
- ``comparison``：结果元数据比较基础；
- ``json_encoding``：通用 JSON 安全编码 / 解码。

本包不重新实现任何交易执行逻辑，只做读取、汇总与读写。
"""
