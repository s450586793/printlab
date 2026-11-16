# 在 Docker 中运行 PrintLab 与 Codex

此部署把工作台、项目文件、Codex 执行与 CadQuery/Blender 放到 Linux 中。公开镜像为 `ghcr.io/s450586793/printlab`，当前仅支持 **Linux amd64（x86_64）**；内置 Bambu Studio AppImage 不支持 ARM64。模型服务使用自己的 API 或兼容网关，Home Assistant 可选。

网页可以直接新建项目，在项目对话中提交需求、查看增量结果、补充信息、停止和接续。每个项目保存独立 Codex 会话，源文件、导出、预览与交接持久保留。刷新浏览器不会重新提交任务；服务重启后中断的任务需要明确选择接续。

## 目录与启动

程序安装在 `/opt/printlab`，业务数据放在 `/data`。宿主机数据目录至少包含 `Project/`、`Context/`、`Library/` 和 `Runtime/`。内置项目模板来自程序目录，也可用数据目录的 `Templates/Project` 覆盖。

1. 安装 Docker 与 Compose 插件，克隆 `https://github.com/s450586793/printlab`，进入仓库目录。
2. 复制 `.env.example` 为 `.env`，设置 `PRINTLAB_DATA_DIR`、`PRINTLAB_BIND_IP`、`PRINTLAB_PORT` 和浏览器实际使用的 `PRINTLAB_STUDIO_URL`。默认只对宿主本机发布端口。部署到 NAS 时选择明确的内网监听地址。
3. 执行以下命令。Compose 创建所需目录，`prepare` 服务仅对本部署的数据和可选旧密钥目录设置 UID 1000 与私有权限，随后退出；长期工作台以普通用户运行，不需要 Docker socket。两个服务使用同一镜像。

   ```sh
   docker compose pull
   docker compose up -d
   ```

4. 首次打开工作台的 `/setup`。启动日志只提示初始化码文件路径，使用以下命令在自己的终端读取初始化码；在网页填写该码、管理员账号和至少 12 位的密码。初始化码只用于首次创建管理员，不要粘进公开聊天或日志。

   ```sh
   docker compose exec printlab cat /data/Runtime/private/setup-token
   ```

5. 管理员凭据保存在数据目录的 `Runtime/private/web-auth.json`，重启后继续使用。登录后打开侧栏“设置”，在“模型接入”填写服务名称、API Base URL、Key 和模型 ID；可获取模型列表并测试工具调用，保存后在项目输入区选择服务和模型。

首次启动不需要预建 secret 文件或模型密钥；尚未配置模型时不能发送建模任务。示例中的地址、用户名和模型 ID 都是占位值。不要把 `.env`、`deploy/data`、`deploy/secrets`、初始化码或浏览器登录状态提交到 Git。

已有部署保留原数据与 `./deploy/secrets`。该目录继续只读挂载到工作台的 `/run/printlab-secrets`，入口可自动识别原 `web-auth.json`。需要显式选择其他凭据文件时，在 `.env` 中设置容器内的 `PRINTLAB_WEB_AUTH_FILE` 路径。读取优先级为：显式路径、数据目录凭据、旧挂载凭据，均不存在时进入首次设置。已有账号不需要重新初始化。

镜像包含 Node 22、真实 Linux Codex CLI、Python 3.12、CadQuery、通过虚拟显示渲染的 Blender，以及官方 Bambu Studio CLI 和同版本切片资源。构建阶段检查实际工具可启动。

工作台容器允许 Codex 创建自己的 Linux 工作空间沙盒，单独设置 `seccomp=unconfined` 和 `apparmor=unconfined`；Codex 仍使用 `workspace-write`，不增加宿主目录或 Docker socket 挂载，也不修改 NAS 全局设置。

## 镜像更新与源码构建

默认 Compose 只拉取镜像。`main` 更新发布 `latest` 和 `sha-` 标签，`v*` 标签发布对应版本；Pull Request 仅构建，不登录或推送 GHCR。发布流程附带 SBOM 和构建来源记录。发布者需将 GHCR 包设为公开，其他使用者才可匿名拉取。

稳定部署可在 `.env` 中把 `PRINTLAB_IMAGE` 设为已发布版本或固定的镜像 digest；两个服务会共同使用它。更新前备份数据目录与所用镜像 digest，再执行 `docker compose pull` 和 `docker compose up -d`。备份包含凭据和项目，保存在自己的受限目录。

需要从源码构建时，显式叠加 `compose.build.yaml`：

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

此入口给两个服务使用同一本地镜像 `printlab:local`，可通过 `PRINTLAB_BUILD_IMAGE` 修改名称。如构建网络需要代理，设置 `PRINTLAB_BUILD_PROXY`。这个值只用于镜像构建，项目运行不依赖构建代理。

若 NAS 的镜像加速站无法获取基础镜像，可仅为此部署设置 `PRINTLAB_NODE_IMAGE` 与 `PRINTLAB_PYTHON_IMAGE`，使用经过确认的官方镜像入口，不必修改全局 Docker 镜像源。

第三方组件的版本、许可和源码入口见 [镜像分发说明](../THIRD_PARTY_NOTICES.md)。镜像内 `/usr/share/doc/printlab` 保存软件清单和 notices；Bambu Studio 的固定来源另存于 `/opt/bambu-studio/PRINTLAB-SOURCE.json`。

## 从 Mac 迁移

先保留 Mac 工作空间快照，再复制 `Project/Context/Library` 和必要的 `Runtime` 业务配置。HA OAuth 文件位于 `Runtime/private/homeassistant.json`，需要可写以保存续期令牌。不要复制 Mac 虚拟环境、node_modules、应用程序或工具二进制。

入口会保留已有配置和项目，初始化缺失的规则文档，并把 CAD、Blender、切片程序／资源和工作台地址改为明确的容器设置。只有设置对应环境变量时才覆盖路径；自定义工艺参数和项目文件继续保存在 `/data`。HA 提供设备状态、摄像头和缓存文件；展示缓存不表示模型已复制成本地长期档案。

## Codex 接入

PrintLab 后端启动 `codex app-server`，通过本地 stdio 协议管理每个项目的 thread/turn、进度和确认。所选 API 为 Codex 提供模型推理；实际读写项目文件、运行 CadQuery/Blender 和导出模型由容器里的 Codex 执行。切换模型不需要为每种模型安装不同的建模工具。

在“模型接入”中可保存多个服务，每个服务配置多个模型 ID，并指定默认接入。Base URL 可以包含 `/v1` 或网关路径，不包含 `/responses`。首期要求 **OpenAI Responses 协议与 function 工具调用**；只提供 Chat Completions 或纯文本回复的服务暂不能用于此执行链路。图片理解还要求模型本身支持图片。模型列表可访问、普通聊天成功，都不等于已完成建模验收。

密钥由登录后的同源写入接口接收，只保存在 `/data/Runtime/private/model-providers.json`，权限为 600；页面读取只返回是否已配置，不返回密钥或尾号。编辑时密钥留空保留原值。试连不会保存候选配置。不要在项目消息中粘贴密钥。

每次修改配置产生新版本，已排队和运行的任务继续使用原版本；接续历史任务也继承其原接入。每个版本有独立的私有 Codex 目录，历史密钥保留用于旧任务接续，停用服务只阻止新选择。备份数据目录时应将它作为含凭据的私密备份。项目文件和网页消息历史共享，切换接入后原生模型会话按服务版本分别保存，新会话读取项目资料与最新交接。

各种兼容服务均在网页中作为普通接入添加，不依赖作者的账号或专用配置。升级会保留已有 Codex 会话和配置，以便接续历史任务；新增接入不会自动切换已有项目。

项目卡片和项目顶部显示累计 Token 与费用估算，点击顶部统计可展开输入、缓存、输出、本轮用量和按模型的单价依据。统计使用 Codex 原始请求用量，按响应编号去重；取消、失败和接续保留已经产生的用量。历史会话中的原始用量记录在核对项目目录、会话和轮次归属后只读补齐，不改写旧任务。缺失记录标为“未记录”或“部分”，不视为免费。

网页添加的服务按接入分别统计，相同模型名不自动套用其他服务的单价。缓存命中是输入的子集，推理是输出的子集，不重复计费；上下文占用不用于估算。金额表示已记录用量的估算费用，不预测后续未知任务，也不代表服务商实际扣费。没有配置单价的接入显示价格待配置。

需要按服务商的实际单价估算，可由管理员在数据目录新建 `Runtime/agent-pricing.json`（不放 API key），无需重启。数值单位为每百万 Token，币种为三字母代码；没有自动汇率换算。`provider-xxxxxxxxxxxxxxxx` 替换为该服务的 ID（设置 API 返回的 `id`），`your-model-id` 替换为实际模型 ID。非默认服务等级使用 `模型名:等级` 配置键。修改单价后，页面会按新单价重新估算所有已记录用量；这不是保存历史结算金额的账本。

```json
{
  "providers": {
    "provider-xxxxxxxxxxxxxxxx": {
      "currency": "USD",
      "models": {
        "your-model-id": {
          "input_per_million": "1",
          "cached_input_per_million": "0.1",
          "output_per_million": "2"
        }
      }
    }
  }
}
```

同一项目同时只接受一个修改任务。请求编号避免重复创建项目或重复发送消息。任务与事件落盘在 `Runtime/agent`；作品仍保存在各项目目录中。工具、文件和输入权限由服务端固定，浏览器不能指定任意执行程序或工作目录。

项目图册直接显示分类、搜索和“新想法”。图册与总览的每张项目卡片都有独立“删除”按钮，确认后从工作台移除项目；服务器保留项目文件和对话，项目编号不会复用。Codex 正在运行、切片或打印尚未结束、发送结果待核对时拒绝删除。删除后待打印和购物清单隐藏该项目的需求，已有库存与打印历史保留。

目前没有回收站界面。需要恢复时，管理员先备份对应的 `Project/Pxxx-名称/project.json`，移除其中的 `deleted_at` 字段后刷新工作台；保留原 ID 和目录，不另建同编号项目。

项目页同时显示左侧对话与右侧模型预览，无需切换标签。左右独立滚动，输入区固定在对话下方并可手动展开。右侧可选择 GLB、STL、3MF 模型并下载；尚未登记正式版本的附件模型标为“项目文件”。默认跟随当前成果，手动选定的模型在刷新后保持选择。全部文件、项目说明、打印准备和历史记录收在可展开区域中。执行记录默认收起。“专注对话”可隐藏导航、标题与右侧预览，按 Esc 返回；这些操作不重建项目会话。手机采用上对话、下预览，可向下滚动查看模型，键盘弹出时会按可见窗口调整对话高度。

新想法和项目对话均支持参考图片：选择文件、拖入或粘贴 PNG、JPG、WebP，每张不超过 12 MB。图片经过解码检查并清除元数据，保存在项目的 `Sources/reference-images` 中，以实际图像输入传给 Codex；聊天记录保留附件，刷新后仍可查看。

“添加附件”可上传 STL、STEP、3MF、PDF、Word、表格、ZIP 和源文件，每个文件不超过 100 MB，不限制附件数量，也可只发送附件。文件按原始字节保存在项目的 `Sources/attachments` 中，聊天记录提供下载入口。上传失败或取消时保留草稿和已完成的上传，重试不会重复创建项目。

Codex 通过服务器确认的项目内路径读取文件；容器提供 PDF、DOCX 和 XLSX 读取库，以及已有的建模工具。ZIP 等压缩包不会在上传时自动解压，附件也不会自动执行。能否直接解析其他专有格式取决于容器内可用的工具；上传成功表示文件已保存。

创建项目不预先分配打印机。当前版本生成可核对的模型后，在右侧“打印准备”中按尺寸包络、已声明的材料和已知设备能力筛选，再保存该版本的分配。未知能力或超限会显示原因；初筛不替代实际切片的摆放、支撑和喷嘴检查，已有切片任务的设备绑定保持原样。

## 内置离线切片

Linux x86_64 镜像从拓竹官方固定下载 Bambu Studio `02.09.01.70` Ubuntu 22 AppImage，校验 SHA-256 后解包；提供 GTK／WebKit 运行库、包内依赖和 Xvfb，无需 FUSE，也无需每个项目重复安装。上游来源、版本和校验值保存在 `/opt/bambu-studio/PRINTLAB-SOURCE.json`。

稳定入口为 `/usr/local/bin/printlab-slicer`，官方资源为 `/opt/bambu-studio/resources/profiles`，由 `PRINTLAB_SLICER_PATH`、`PRINTLAB_SLICER_PROFILES` 指定。启动时进行有超时限制的实际 CLI 自检；`printlab doctor` 返回程序缺失、资源缺失、缺运行库或可用状态。切片不可用时仍可打开工作台和建模。

首期自动选择支持 A1／P1P、0.4 mm 喷嘴、单色 Generic PLA／PETG、0.20 mm Standard 和 Textured PEI Plate。先完成模型及检查，并在右侧“打印准备”将当前版本分配给相应打印机，再让项目 Codex 执行：

```sh
printlab slice P003 --material PLA
printlab inspect-slice P003 JOB_ID
```

`P003` 和 `JOB_ID` 替换为实际项目和返回的任务编号。材料必须明确选择；PETG 使用 `--material PETG`。保留原来的 `--machine`、`--process`、`--filament` 手动参数，不能与 `--material` 混用。其他喷嘴、材料、多色或板型不由此自动配置支持。

每次切片解析官方继承并保存配置、来源 SHA、模型版本、日志、`job.gcode.3mf` 及实际切片器返回的耗时和克数。结果在右侧现有打印准备与文件下载中查看。`offline_ready` 只表示离线文件已生成并通过检查；实际喷嘴、供料、板面和设备状态仍未核实，不构成打印许可。HA 自动发送继续关闭。

## 可选 Home Assistant 接入

先在自己的 Home Assistant 中安装并配置 `ha-bambulab`，再在其个人资料的安全设置中创建长期访问令牌。打开 PrintLab“设置”中的“Home Assistant”，填写容器可以访问的 HA 地址和令牌，测试连接后保存。没有发现打印机时也可先保存连接，待 HA 配好设备后刷新。

令牌保存在服务端的受限文件中，页面不会回显。同一地址编辑时，令牌留空可以保留现有凭据；“断开并移除凭据”会解除连接，不影响工作台建模功能。已有 OAuth 接入可继续使用原数据，数据目录备份也应包含其私有凭据。

## 设备面板

HA 设备页同时显示各台打印机，桌面为左右两列，手机上下排列。每台上方显示摄像头快照，下方显示任务状态、进度、喷嘴与热床温度、剩余时间、层数及 AMS 装料；装料诊断和余量依据可展开查看。画面和参数每 30 秒刷新，也可手动刷新。

各台设备独立读取，一台离线或摄像头失败不影响另一台。读取失败时标明上次数据，未知参数保持为空。状态依据 HA 报告，100% 进度不会被当成正在打印。

## 访问与能力边界

容器入口保护全部项目、摄像头、文件与 Codex 接口。若通过 HTTPS 反代访问，设置准确的 `PRINTLAB_STUDIO_URL`，并仅对实际反代地址开启 `PRINTLAB_PROXY_HEADERS=1`、`PRINTLAB_FORWARDED_ALLOW_IPS`；不应信任任意来源的代理头。

访问工作台会先进入 PrintLab 登录页，使用 `web-auth.json` 中配置的账号和密码登录。登录状态保存在 HttpOnly Cookie 中，有效期为 12 小时；页面右上角可退出登录。会话签名密钥保存在 `Runtime/private/web-session.key`，服务重启后仍可接续；修改登录凭据并重启服务后，旧登录状态失效。私密项目接口未登录时返回 401，兼容已有 Basic API 客户端，但不会触发浏览器账号密码弹框。

Mac 的官方 Studio 界面自动操作和 MLX/Metal 图生 3D 不能直接在 Linux 使用。Linux 内置 CLI 提供上述已支持范围的离线切片；完整可编辑 X2D 工程导出仍沿用原有流程。HA 模式的自动发送继续保持未开放，不触发实体打印。

验收需包含真实网页任务创建、两轮同项目接续、实际 CAD 导出和预览、HA 设备与缓存读取，以及重启后的文件和对话恢复。健康检查成功只证明服务运行。
