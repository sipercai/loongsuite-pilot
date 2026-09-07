# AgentCore 专用 Pilot 补丁栈：跟进上游指南

维护分支：`fix/20260907_qoder_managed_context_paths`（sipercai fork）。
本文件只说明差异、契约及回归门禁，不携带账号、上报凭证或预发 Agent 配置。

## 上游基线与补丁顺序

2026-09-07 实际 fetch 的 Alibaba `main` 为
`4631bd760a586e9ac4e79bd992f6ffc120334f96`，已经包含 QoderCN duration/token 修复 #375。
它也是本分支基线；本次没有必要 rebase，也没有改写已推送历史。

| 顺序 | 独立提交 | 能力 | 主要冲突面 |
| --- | --- | --- | --- |
| 1 | `833685314b0ac7c76db5f1b541b9964639ee3d09` | UUID→属性注册、TTL/原子写、Stop Hook 关联 | `src/cli/invocation-context.ts`、`assets/hooks/shared/invocation-context.mjs`、`qoder-hook-processor.mjs` |
| 2 | `3d2da7af8240f3c819e0f017e7a5ddaf557b95f4` | 可指定本地 Context root、托管 intercept 路径一致 | 注册/Hook 的路径解析、token intercept writer/reader |
| 3 | `ed39e70b6e4f0fb8f0643d27ad8050f295b181f5` | 注册命令轻量分流，公共 argv 不变 | `build.mjs`、`src/entrypoint.mjs`、安装产物布局 |
| 4 | `eca639f` | JSON 自定义属性值保留逗号 | `src/inputs/base/canonical-hook-record.ts` |

第 4 项修复是独立的采集归一化问题，不要与 CLI 性能修改混在同一提交。
后续纯文档提交不属于功能补丁，重放时可以最后单独带上。

## Runtime/Pilot 边界（跟进上游时必须保留）

Runtime 负责：

1. 从当前 Run 获取 Task/Subtask；不把每轮属性存到 WarmQuery 的静态 Options 中。
2. 每次 SDK attempt 新建 UUID；先注册，再把同一 UUID 放入 SDKUserMessage。
3. JSON 属性通过 stdin 传入，不编码进 UUID、prompt 或 shell 字符串。
4. 注册失败有时间预算，记录观测降级，业务 Query 继续。

Pilot 负责：

1. 公共命令：`node dist/index.js invocation-context put --agent <id> --message-uuid <uuid>`。
2. Global agent ID 为 `qoder`，CN 为 `qoder-cn`，两端同一套 Context 协议。
3. Context v1：UUID、agent ID、属性、created/expires 时间；默认 TTL 48 小时、私有文件 0600、原子 create-if-absent、重复相同值幂等、冲突拒绝。
4. `LOONGSUITE_PILOT_INVOCATION_CONTEXT_ROOT` 指向本地可写目录；CLI 与 Hook 必须一致。未设置仍使用 `<dataDir>/state/invocation-contexts`。
5. Stop Hook 从 transcript user.uuid 查询 Context，动态属性优先于旧进程静态值；不消费/删除 Context，允许延迟和重试。
6. JSON 属性进入 CN/Global canonical records 后继续保留，不能再按逗号分隔字符串处理。
7. 托管 preload 与 Collector 必须用同一 intercept 文件：二者使用 `LOONGSUITE_PILOT_DATA_DIR` 下的 `logs/qodercli-intercept.jsonl` 或 `logs/qoderclicn-intercept.jsonl`，不能各自依赖不同 HOME（见 managed-context-paths 文档）。

## 轻量产物边界

- `dist/index.js`：轻量分派，不能顶层静态 import Orchestrator/native guard/logger。
- `dist/invocation-context.js`：自包含注册模块，只依赖 Node builtins。
- `dist/collector.js`：原应用 bundle，保留 native guard、默认 Collector、deploy 和其他 CLI 行为。
- 发布完整 `dist/`，不能按旧认知只搬 index.js。Runtime 调用路径不变。

若上游已经提供等价的轻量 CLI，优先复用，删除本分支分派补丁；不要长期维持两套注册实现。

## 跟进 Alibaba 的安全步骤

1. 保存当前固定 SHA 和已验收镜像 digest，确保工作目录干净。
2. fetch Alibaba main；逐项检查上表能力是否已上游化，不能只看文件名是否存在。
3. 在新候选分支基于上游 main 重放仍缺少的提交，按 1→2→3→4 的依赖顺序处理。
4. 对 build、Hook UUID 和路径解析冲突做语义合并；不能用整文件 ours/theirs 覆盖。
5. 跑下面的回归，完成两端真实 Runtime 验证，再推候选分支、构建新不可变 tag。
6. 保留原已验收分支用于对照；不要为了同步上游强推共享分支或直接改预发 tag。

可在新候选分支使用 `git cherry-pick` 选择缺失提交；若选择 rebase，也应先在候选分支演练。
上游已实现的补丁应跳过，同时保留相应测试作为契约检查。

## 必跑门禁

```sh
npm run typecheck
npm run test:invocation-context-artifact
npx vitest run tests/unit/cli/invocation-context.test.ts \
  tests/unit/inputs/canonical-hook-invocation-attributes.test.ts \
  tests/unit/inputs/qoder-trace-input.test.ts \
  tests/unit/inputs/qoder-cn-trace-input.test.ts \
  tests/unit/hooks/qoder-turn-boundary.test.mjs \
  tests/unit/hooks/qodercli-token-intercept.test.mjs \
  tests/unit/deploy/native-deps-guard.test.mjs
```

安装态：同一不可变镜像，分别以 site=global/cn 创建真实 Runtime，不能靠 tag 名字判定站点。
至少验证冷启动、A→B WarmQuery、无属性不串值、中文/逗号/等号、重启恢复，以及并发 Session。
每轮关联 Run→message UUID→Context→Hook turn→Collector trace ID→OTLP Span；不能用模型文本包含 Task ID 替代 Span 属性。

本地环境使用 Linux 原生数据 volume，避免宿主机共享目录 EACCES 干扰；这不意味着能省略生产 CSI 挂载的验证。
本地通过、镜像发布成功、预发 Sandbox 健康、云端 Trace 回查是四项独立结论。

## 版本选择

Runtime Dockerfile 的 `LOONGSUITE_PILOT_REPOSITORY` 指向本 fork（仅开发验证），
`LOONGSUITE_PILOT_REF` 必须是候选分支经测试后的完整 40 位 SHA。
正式切回 Alibaba 时同时检查 repository 与 ref，不使用浮动 main/latest，不依赖本地容器手工替换文件。
