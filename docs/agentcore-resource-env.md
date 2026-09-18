# AgentCore Pilot Resource 环境变量

在启动 Pilot Collector **之前**向其进程环境传入：

```sh
export OTEL_RESOURCE_ATTRIBUTES='service.namespace=ws-example,service.app.owner_id=001234'
```

配置加载时读取一次，适用于 Qoder Global 和 CN。属性进入导出 Span 的
`Resource.attributes`，不是 Span attributes。修改环境变量后需要重启 Collector；
仅向已经运行的 Qoder Query 子进程注入环境变量，不会改变 Collector 的 Resource。
Runtime 如果为 Collector 显式构造 `env`，必须保留此变量。

## 解析与优先级

解析对齐 [OpenTelemetry Python OTELResourceDetector](https://github.com/open-telemetry/opentelemetry-python/blob/main/opentelemetry-sdk/src/opentelemetry/sdk/resources/__init__.py)：

- 逗号分隔条目，首个 `=` 分隔键和值，分别去掉首尾空白。
- 只对值做百分号解码；`+` 保持 `+`。值中的逗号编码为 `%2C`。
- 所有值保留字符串，包括前导零和空值；重复键取最后一个值。
- 没有 `=` 的条目跳过；错误百分号转义原样保留，非法 UTF-8 替换为 `�`。
- 不记录原始环境变量，避免日志暴露标识信息。

OTel JS 新版本与 Python 的非法输入处理不同；本补丁明确选择 Python 行为，
不是复刻最新 JS EnvDetector。普通自定义属性按 Pilot 现有策略：环境变量覆盖
`otlpTrace.resourceAttributes` 的同名值。这是 Pilot 配置优先级，不是 Python
`Resource.create()` 的合并优先级。

`service.namespace` 不再是保留字段，用于传入 AgentCore Workspace ID。
其优先级为：`OTEL_RESOURCE_ATTRIBUTES` > `otlpTrace.resourceAttributes` >
事件中配置为投影到 Resource 的同名属性 > 默认值 `loongsuite-pilot`。
显式空字符串同样保留，不会回退到默认值。事件不能覆盖启动时明确指定的 Workspace。
这项优先级仅适用于 `service.namespace`，其他字段保持现有规则。

`service.name`、`service.instance.id` 等保留字段仍由
Pilot 管理，不能通过该变量覆盖；本补丁不新增 `OTEL_SERVICE_NAME` 支持。
它也不会启用采集或改变 CMS workspace、上报 endpoint、认证和路由。

## 与 Task/Subtask 的边界

Workspace/owner 是 Collector 生命周期内的 Resource 属性。按 Run 变化的
Task/Subtask 继续使用 UUID Invocation Context 写入 Span attributes，不能改用
此进程级环境变量，否则 WarmQuery/并发调用会串值。

## 验证

```sh
npm run typecheck
npx vitest run tests/unit/core/resource-env.test.ts tests/unit/core/config-loader.test.ts \
  tests/unit/flushers/otlp-trace-flusher/cp5-no-double-emit.test.ts
npm run test:invocation-context-artifact
```

覆盖解析、配置合并及两种 agent identity 的真实转换器导出 Resource；导出测试使用
捕获 exporter，不访问云端，也不代表真实 Qoder Runtime E2E 或 ARMS 回查已通过。
旧发布包不会因源码提交自动更新，必须重新构建版本才能获得该能力。
