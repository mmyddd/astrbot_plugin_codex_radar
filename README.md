# astrbot_plugin_codex_radar

AstrBot 插件：**Codex 智力效率雷达**。查询 [codexradar.com](https://codexradar.com/) 的模型智力效率数据与 72 小时 IQ 历史曲线。

## 指令

| 指令 | 说明 |
|------|------|
| `降智雷达` | 抓取 codexradar.com 的智力效率数据：耗时、分数（IQ）、花费、综合成本曲线、数据更新时间。按「模型 × 思考强度」完整列出，并发送「综合成本 × IQ」曲线图片。 |
| `雷达历史` | 抓取 deng.codexradar.com 的 72h IQ 历史曲线，发送曲线图片，并列出各模型及思考强度明细。 |
| `雷达历史 <模型>` | 只输出指定模型的近 72h 全思考强度历史（曲线图 + 各强度走势）。支持别名：`sol` / `terra` / `luna` / `5.5` / `d4flash` / `d4pro` / `grok` / `k3` / `glm` 等（共 11 模型），也支持完整模型名或唯一子串；无法唯一匹配时返回可用模型列表。 |

失败时返回**可诊断的错误信息**（阶段 / 地址 / 状态码 / 原因 / 重试次数），**不会返回虚构数据**。

## 数据来源（实现前已实际核实）

| 数据 | 接口 | 说明 |
|------|------|------|
| 智力效率 | `https://codexradar.com/api/intelligence-efficiency` | 主接口：旧 schema=1（`combos`/`tasks`/`cells`）与新分布式 points 兼容 |
| 智力效率 | `https://api.codexradar.com/api/v1/intelligence-efficiency` | 新分布式接口（schema=3，`points` 列表，已归一） |
| 智力效率 | `https://api.codexradar.com/api/v1/table` | 大表接口（schema=1 全量 combos，含 37 档位） |
| 智力效率（备用） | `https://codexradar.com/data/intelligence-efficiency.json` | 站点前端内置的静态快照（新分布式 schema=2，`points` 格式），主接口失败时自动回退 |
| 72h 历史 | `https://api.codexradar.com/api/v1/iq-history?v=20260719-language-1` | deng.codexradar.com 前端实际调用的接口；按模型 / 模型@强度 返回每小时观察点 `{ts, score, n}`，`latest:` 前缀为实时监控系列 |

聚合算法与站点前端一致：

- **IQ 分数** = 通过题数 / 有效题数 × 150（100% 通过 → 150）
- **耗时** = 每题最新一次有效运行 `duration_sec` 的均值（分钟）
- **花费** = 每题最新一次有效运行 `actual_cost_usd` 的均值（USD；ultra 档仅统计 `cost_complete=true`）
- **综合成本指数** = 平均价格 × (平均耗时分钟 / 10)^ln(2.5)/ln(1.35) × 100，再按全图最高值归一为 100（站点「2.5×价格≈1.35×速度」口径）
- **散点图坐标**：与站点一致，x 轴为相对综合成本指数（对数刻度；第二小值 ≥ 4× 最小值时启用断轴，避免大部分点挤在左侧），y 轴为 IQ 分数（0-150，上限取站点 niceMax 整点）；同一模型的各思考强度按 off→ultra 连线（off 为关闭推理，仅 deepseek 系），点形状区分强度；当前共 11 模型 37 档位（gpt-5.6 全系 6+6+5、gpt-5.5 2、deepseek 系 4+4、grok 4、k3 3、glm 3）

## 缓存策略

- 进程内 TTL 缓存，避免每次指令都重复请求远端。
- 默认：`降智雷达` 300 秒、`雷达历史` 600 秒（站点约每几分钟更新一次；72h 曲线每小时新增一个观察点）。
- 每次响应都会标注数据的**更新时间**（数据生成时间 / 最新观察时间）。
- 可在 WebUI 插件配置中调整（见 `_conf_schema.json`）。

## 曲线生成方式

1. 首选 **AstrBot 内置 `html_render`**（Playwright）将内联 SVG 渲染为 PNG；
2. 失败时降级为 **Pillow** 直接绘制 PNG（AstrBot 自带 Pillow）；
3. 仍失败或关闭图片开关时，降级为**文本表格 + ASCII 走势图**。

## 配置（_conf_schema.json）

| 键 | 默认 | 说明 |
|----|------|------|
| `radar_cache_seconds` | 300 | 降智雷达缓存秒数 |
| `history_cache_seconds` | 600 | 雷达历史缓存秒数 |
| `request_timeout_seconds` | 12.0 | HTTP 超时（秒） |
| `request_retries` | 2 | 超时 / 5xx / 429 时重试次数 |
| `history_hours` | 72 | 历史曲线时间范围（下拉选项 24 / 48 / 72，对应 deng 站 IQ_TREND_HOUR_OPTIONS 三档） |
| `send_chart_image` | true | 优先发送曲线图片 |

## 开发与测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v          # 单元测试
python scripts/validate_live.py     # 抓取真实接口验证解析与出图
```

测试覆盖：指令注册与路由、正常数据解析（含站点口径公式）、网络请求失败、数据字段缺失 / 结构变化。

## 已知限制

- 目标站点未提供正式 API 文档，接口与 `v=` 缓存参数可能随前端改版而变化；主接口失败时插件自动回退静态快照，仍失败则返回诊断信息。
- `deepseek-v4-flash` / `deepseek-v4-pro` / `dsh-deepseek` 等深研系列在无样本时段会显示「暂无数据」；`k3`（kimi-code）、`glm-5.3`（zcode）、`grok-4.6` 的 IQ 历史按站点实际观测逐步接入，缺失时段同样标注「暂无数据」。
- 图表中文依赖渲染环境字体（html_render 无此问题；Pillow 降级路径会尝试常见中文字体，缺失时可能显示为方块）。
- 综合成本曲线为站点「综合成本 × IQ」散点口径；72h 曲线按每小时观察点绘制。
