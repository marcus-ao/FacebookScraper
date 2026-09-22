# FB / IG 内容本地化

归档美国站 Facebook / Instagram 图文，生成德语文案和图片，供运营审校并按北京时间选择发布时刻，通过 Meta Business Suite 创建单渠道排期。审校台会在每个时刻旁并排显示德国受众的柏林当地时刻。

## 安装与启动

首次安装在仓库根目录运行（需要 Python、Node.js/npm；`setup.bat` 包含全量离线测试）：

```powershell
scripts\setup.bat
scripts\run_web.bat
```

审校台本地地址为 <http://127.0.0.1:8765>。源码服务机用 `scripts\run_web_lan.bat`，入口与端口读取 [ops/service-machine.network.json](ops/service-machine.network.json)；改址运行 `scripts\update_service_address.bat`，同时同步新飞书卡片入口。已发送的历史卡片链接不会随配置改写，操作说明见 [MANUAL_STEPS §13.1](docs/MANUAL_STEPS.md#131-源码服务机一键改址)。前端开发与故障定位见 [web/README.md](web/README.md)。

日常更新按 [MANUAL_STEPS §13](docs/MANUAL_STEPS.md#13-更新并启动审校台) 拉取源码并重启；启动脚本会安装锁定前端依赖、构建后启动 Web，无需重复运行安装脚本。旧受管实例维护另见 [§15](docs/MANUAL_STEPS.md#15-服务机部署与日常更新)。

业务配置在 [config.toml](config.toml)，密钥按 [.env.example](.env.example) 填入 `.env`。接续已有数据时使用 [config.local.example.toml](config.local.example.toml) 绑定本机路径，恢复步骤见操作指南。

## 检查

按 [AGENTS 第四节](AGENTS.md#四按影响选测试默认不跑全量) 选择与改动直接相关的检查。例如解析模块的隔离验证：

```powershell
scripts\run_python.bat -m tools.test_offline --only tests_parse
```

运行状态按需查询：

```powershell
scripts\run_pipeline.bat preflight --json
scripts\run_scheduler.bat --preview
```

跨模块改动确需全量回归时，运行 `scripts\run_python.bat -m tools.test_offline`；当前交付流程不依赖 GitHub Actions 或云端运行包。

## 导航

| 内容 | 入口 |
|---|---|
| 业务流程与术语 | [FUNCTIONALITY](docs/FUNCTIONALITY.md) |
| 功能要求与验收状态 | [REQUIREMENTS](docs/REQUIREMENTS.md) |
| 当前现场、数据边界与证据 | [HANDOFF](docs/HANDOFF.md) |
| 浏览器、调度、付费与发布操作 | [MANUAL_STEPS](docs/MANUAL_STEPS.md) |
| Agent 协作约定 | [AGENTS](AGENTS.md) |

源码按 `routes/` 抓取、`localize/` 本地化、`publish/` 发布、`pipeline/` 编排、`core/` 共享契约和 `web/` 审校台划分。`archive/` 与 `state/` 保存业务数据，不属于可随构建清理的产物。
