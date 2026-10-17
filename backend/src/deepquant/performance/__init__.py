"""绩效分析、结果序列化与可复现性。

子模块（按需显式导入，避免与 backtest 产生循环依赖）：
- ``metrics``：绩效指标与交易统计（口径定义处）；
- ``curve``：净值曲线统计（累计/年化/波动/回撤的唯一定义处，基准与组合层共用）；
- ``benchmarks``：基准策略 B0 现金 / B1 买入持有 / B2 指数；
- ``portfolio_metrics``：组合层夏普、换手率、成本占比、证据等级；
- ``serialize``：BacktestResult 的 JSON 往返序列化；
- ``reproducibility``：数据集摘要与策略元数据；
- ``comparison``：结果元数据比较基础；
- ``json_encoding``：通用 JSON 安全编码 / 解码。

本包不重新实现任何交易执行逻辑，只做读取、汇总与读写。
"""
