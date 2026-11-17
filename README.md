# PrintLab

简体中文 · [English](README.en.md)

把一个想法，接着做成一件拿得起来的东西。

PrintLab 是运行在 Docker 中的 3D 打印工作台，面向 Linux / NAS 的 **amd64（x86_64）** 设备。左侧与 Codex 对话，右侧查看模型与版本；需求、参考图片、附件、源模型和切片结果保存在同一项目中。模型 API 由你在网页中配置，Home Assistant 可选。

## Docker 快速开始

安装 Docker 与 Compose 插件后，执行：

```sh
git clone https://github.com/s450586793/printlab.git PrintLab
cd PrintLab
cp .env.example .env
docker compose pull
docker compose up -d
```

默认从 Docker Hub 拉取 `docker.io/s450586793/printlab:latest`，仅对宿主本机开放 <http://127.0.0.1:8018>。NAS 或其他电脑访问时，先在 `.env` 中设置 `PRINTLAB_BIND_IP`、`PRINTLAB_PORT`，并将 `PRINTLAB_STUDIO_URL` 改为浏览器实际访问的地址，再启动服务。数据默认保存在 `./deploy/data`，可通过 `PRINTLAB_DATA_DIR` 修改。

首次访问 <http://127.0.0.1:8018/setup>，在部署终端读取初始化码：

```sh
docker compose exec printlab cat /data/Runtime/private/setup-token
```

在网页输入初始化码、管理员账号和至少 **12 位**的密码，完成初始化后登录。无需提前创建密钥文件；账号和项目会在重启、更新镜像后保留。端口、内网访问、反向代理、旧数据迁移、备份及源码构建见 [Docker 部署指南](docs/DOCKER.md)。

## 配置模型并创建项目

登录后打开侧栏“设置” → “模型接入”，填写服务名称、API Base URL、API Key 和模型 ID。Base URL 可包含 `/v1` 或网关路径，不包含 `/responses`。测试工具调用并保存后，在项目输入区选择服务和模型；可添加多个接入。

模型服务必须支持 **OpenAI Responses 协议与 function 工具调用**。图片理解还要求模型支持图片。容器内的 Codex 负责读写项目文件、运行 CadQuery / Blender 并导出模型；尚未配置可用接入时不能发送建模任务。

点击“新想法”描述用途和尺寸，可以同时添加参考图片或附件。PNG、JPG、WebP 可选择、拖入或粘贴；STL、STEP、3MF、PDF、Word、表格、ZIP 和源文件可通过“添加附件”上传。上传的文件与对话一起保存，压缩包不会自动解压。

## 工作台里有什么

- **左对话、右预览**：边讨论边查看 GLB、STL、3MF 模型，选择版本并下载文件；手机采用上对话、下预览。
- **持续的项目上下文**：保存需求、图片、附件、源文件、执行记录与交接，支持停止和接续；刷新页面不会重复提交任务。
- **建模与切片工具**：镜像包含 Codex、CadQuery、Blender 和 Bambu Studio CLI，项目共享容器内的工具。
- **项目与打印准备**：管理作品版本、材料、打印机分配、切片结果、配件清单和打印反馈。
- **可选设备连接**：通过 Home Assistant 查看拓竹打印机状态、AMS 装料与摄像头快照。

从需求到实物的参考流程为：用途与尺寸 → 外形参考 → CAD → 查看与修改 → 材料选择 → 切片比较 → 核对打印文件 → 实物反馈。完整的原版协作流程见 [工作流](Context/workflow.md) 和 [角色提示词](docs/ROLES.md)。

## 内置切片与设备边界

Linux 镜像内置 Bambu Studio `02.09.01.70` 的离线切片程序与资源。当前自动配置支持 **A1 / P1P、0.4 mm 喷嘴、单色 Generic PLA / PETG、0.20 mm Standard、Textured PEI Plate**。先在项目右侧“打印准备”分配当前版本的打印机，再让项目 Codex 执行切片；结果包含可下载的 `job.gcode.3mf`、耗时和用量。

其他喷嘴、材料、多色和板型需要另行核对配置。离线文件生成成功只代表切片与文件检查通过，仍需核对实际喷嘴、供料和板面。**Home Assistant 自动发送打印尚未开放**。详细命令与限制见 [内置离线切片](docs/DOCKER.md#内置离线切片)。

需要设备状态时，先在自己的 Home Assistant 中配置 `ha-bambulab`，再到 PrintLab“设置” → “Home Assistant”填写 HA 地址和长期访问令牌，测试后保存。未连接 HA 也可以建模、预览和离线切片。接入与断开方式见 [Home Assistant 接入](docs/DOCKER.md#可选-home-assistant-接入)。

## 更新与开发

GitHub `main` 更新后，Actions 自动测试、构建并发布 Docker Hub 镜像。在仓库目录执行 `docker compose pull` 和 `docker compose up -d` 更新镜像；更新前备份数据目录。支持固定镜像版本或 digest，也可叠加 `compose.build.yaml` 从源码构建。自行发布所需的 Actions 配置见 [Docker 部署指南](docs/DOCKER.md#镜像更新与源码构建)。

源码包含工作台、原创流程文档、角色提示词与空项目模板。依赖与版本见 [依赖说明](docs/DEPENDENCIES.md)，镜像中的第三方许可及源码入口见 [第三方说明](THIRD_PARTY_NOTICES.md)，验证记录见 [验证说明](docs/VERIFICATION.md)。

## 桌面安装与原版流程

Mac / Windows 的本机安装、桌面工具和原版协作说明继续保留：[安装指南](docs/SETUP.md) · [Windows 指南](docs/WINDOWS.md) · [交互指南](docs/GETTING_STARTED.md) · [Agent 接手指南](docs/AGENT_START.md)。这些文档中的桌面打印能力与 Docker 的支持范围不同，详见 [兼容性与打印](docs/PRINTING.md)。

## 使用许可与署名

本项目基于 **AFangTeam / 阿方（Eric）** 的 [PrintLab](https://github.com/AFangTeam/PrintLab)，原项目是其 E41 视频中使用的 3D 打印工作台与协作流程。本仓库提供 Docker 工作台的通用部署与网页配置。

PrintLab 原创源码和文档采用 **PolyForm Noncommercial 1.0.0**。允许在许可规定的非商业用途下使用、修改和再发布，再发布须附上许可及 `NOTICE.txt` 中的署名声明。商业用途需要另行取得 AFangTeam 授权。

许可全文见 [LICENSE.md](LICENSE.md)，署名要求见 [NOTICE.txt](NOTICE.txt)。第三方软件遵循各自的许可。
