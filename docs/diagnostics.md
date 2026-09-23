# 排查核验失败和模型超时

日志自动写入项目的 `logs/crosscheck-进程号.jsonl`，每行是一条 JSON 事件。重启或自动重载后会生成新进程的日志文件。日志从启用后开始记录，不能恢复以前没有保存的模型响应。

## 找到本次核验的日志

报告编号与日志中的 `request_id` 对应。鼠标悬停在报告编号上可查看完整 ID；请求失败时页面也会显示诊断 ID。HTTP 响应头 `X-Request-ID` 提供相同的 ID。

在项目目录查看最近修改的文件：

```bash
ls -lt logs/
```

按报告编号筛选（输入完整 ID 或报告显示的前 8 位）：

```bash
read -r diagnostic_id
rg -i -- "$diagnostic_id" logs/
```

查找模型失败和规则回退：

```bash
rg '"event": "(llm_request_failed|llm_attempt_failed|llm_rules_fallback|evidence_model_attempt_failed|evidence_model_rules_fallback)"' logs/
```

| 事件 | 用途 |
| --- | --- |
| `http_request_started` / `http_request_finished` | HTTP 状态、请求总耗时、响应是否完整结束；SSE 的 200 不等于核验成功 |
| `verification_started` / `verification_completed` / `verification_failed` | 核验总耗时、最终解析方式与证据判断方式 |
| `verification_progress` / `stage_finished` | 当前阶段和相邻进度节点之间的耗时 |
| `llm_request_started` | 模型名、主备角色、服务域名、实际超时秒数、输入字符数、是否存在代理环境变量 |
| `llm_http_response` / `llm_response_shape` | HTTP 状态、响应耗时和大小、上游请求 ID、停止原因、内容类型与长度、可用的 token 用量 |
| `llm_request_failed` | ReadTimeout、HTTP 错误、JSON 错误；格式失败时区分空内容、普通文本和类似 JSON 的内容 |
| `llm_attempt_succeeded` / `llm_attempt_failed` | 单个模型最终是否完成解析/判断，是否使用了备用模型；JSON 解析成功后字段校验仍可能失败 |
| `llm_rules_fallback` | 未配置模型或全部模型失败 |
| `evidence_model_attempt_started` / `evidence_model_attempt_succeeded` / `evidence_model_attempt_failed` | 本地训练模型或兼容接口证据判定器的调用结果和耗时 |
| `evidence_model_rules_fallback` | 证据判定器不可用，回退到规则判断 |
| `provider_http_started` / `provider_http_response` | 搜索或抓取服务实际超时、HTTP 状态及等待响应头的耗时 |
| `provider_succeeded` / `provider_failed` / `content_selected` | 检索结果数、正文长度、失败类型和最终选择的内容服务 |
| `report_saved` / `report_save_failed` | 报告是否真正保存，写入耗时 |

每次模型 HTTP 调用还有独立的 `attempt_id`。并行抓取按 `provider` 和 `page_id` 区分。时间统一为 UTC，耗时单位为毫秒。`ReadTimeout` 表示等待读取响应数据超时，并不能单凭该异常断定是模型排队、网络还是代理故障；代理环境变量存在也不证明请求一定经过了代理。

主模型失败、备用模型成功时，日志保留失败过程，页面只显示最终成功。只有全部模型失败才向页面返回回退警告。

## 查看模型为什么没有返回 JSON

默认不记录用户主张、网页正文、提示词、模型原文、请求头或上游错误正文。日志会记录响应长度、内容指纹、结束原因和异常代码位置，不保存异常的任意原始文本。

如果这些信息不足，可在项目 `.env` 中设置：

```dotenv
LOG_LLM_RESPONSE_PREVIEW=true
```

重启服务并重新核验。JSON 提取失败时，`llm_request_failed.response_preview` 会保存最多 2000 字符的模型内容，`preview_truncated` 表示是否被截断。已配置的 API Key 和常见凭据格式会被替换为 `[REDACTED]`。此片段仍可能包含模型复述的业务内容，分享日志前应检查片段。排查结束后可设回 `false` 并重启。

该开关不保存完整响应、不采集模型推理内容，也不改变模型调用、重试、超时或主备顺序。

## 日志文件与保留范围

- 每个进程独立一个活动文件，达到约 5 MB 自动轮转，最多保留 3 个备份（`.1`、`.2`、`.3`）。
- 自动重载产生的旧进程日志会保留；每个进程约 20 MB 的上限不等于整个目录的上限，可定期清理旧进程文件。
- 文件权限为仅当前用户可读写；默认 `logs/` 已加入 `.gitignore`。
- 通过 `.env` 的 `LOG_DIRECTORY` 可以指定其他目录，修改后需重启；目录必须可写。

日志实现位于 [diagnostics.py](../src/crosscheck/diagnostics.py)。测试使用模拟模型和临时数据库，覆盖并发请求关联、HTTP/SSE 报告 ID、主备切换、超时、脱敏和文件轮转。
