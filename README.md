# FB / IG 内容本地化

归档美国站 Facebook / Instagram 图文，生成德语文案和图片，供运营审校并按北京时间选择发布时刻，通过 Meta Business Suite 创建单渠道排期。审校台会在每个时刻旁并排显示德国受众的柏林当地时刻。

## 安装与启动

在仓库根目录运行：

```powershell
scripts\setup.bat
npm.cmd --prefix web/ui ci
npm.cmd --prefix web/ui run build
scripts\run_web.bat
```

审校台地址为 <http://127.0.0.1:8765>。日常启动只需最后一条命令；前端开发与故障定位见 [web/README.md](web/README.md)。

业务配置在 [config.toml](config.toml)，密钥按 [.env.example](.env.example) 填入 `.env`。接续已有数据时使用 [config.local.example.toml](config.local.example.toml) 绑定本机路径，恢复步骤见操作指南。

## 检查

```powershell
scripts\run_pipeline.bat preflight --json
scripts\run_scheduler.bat --preview
scripts\run_python.bat -m tools.test_offline
```

## 导航

| 内容 | 入口 |
|---|---|
| 业务流程与术语 | [FUNCTIONALITY](docs/FUNCTIONALITY.md) |
| 功能要求与验收状态 | [REQUIREMENTS](docs/REQUIREMENTS.md) |
| 当前现场、数据边界与证据 | [HANDOFF](docs/HANDOFF.md) |
| 浏览器、调度、付费与发布操作 | [MANUAL_STEPS](docs/MANUAL_STEPS.md) |
| Agent 协作约定 | [AGENTS](AGENTS.md) |

源码按 `routes/` 抓取、`localize/` 本地化、`publish/` 发布、`pipeline/` 编排、`core/` 共享契约和 `web/` 审校台划分。`archive/` 与 `state/` 保存业务数据，不属于可随构建清理的产物。
