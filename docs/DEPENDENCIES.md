# 第三方工具与镜像分发

PrintLab 源码发布包保留原创工作台、流程、适配接口与项目模板。桌面安装从原作者渠道独立获取第三方工具；Docker 镜像则实际分发 Node、Codex、Python、CadQuery、Blender、Bambu Studio 和工作台依赖。各组件保留原许可与署名，镜像内的软件清单、许可位置和对应源码入口见 [第三方分发说明](../THIRD_PARTY_NOTICES.md)。

| 项目 | 在流程中的作用 | 获取与版本说明 |
| --- | --- | --- |
| [Bambuddy](https://github.com/maziggy/bambuddy) | 设备、材料库存、队列与已捕获历史 | 按原项目安装指南独立运行。PrintLab 本地适配基线为 v1.2.5.7，当前上游接口需重新验证。通过独立 HTTP API 连接。 |
| [Text-to-CAD](https://github.com/earthtojake/text-to-cad) | Codex 建模、模型查看、导出与制造检查 | 按原项目 `docs/install.md` 安装。本地历史验证基线为 0.8.2，当前上游入口已使用 `cad`，以实际 `--help` 为准。 |
| [Bambu Studio](https://github.com/bambulab/BambuStudio) | 正式工程、离线切片与官方发送 | Docker 从官方固定获取 [v02.09.01.70](https://github.com/bambulab/BambuStudio/releases/tag/v02.09.01.70) Ubuntu 22 AppImage 与配套资源；不依赖网络打印插件执行离线切片。桌面发送助手原本地验证版本为 02.08.02.61。 |
| [Blender](https://www.blender.org/download/) | 有机外形、同源预览和动画视图 | 桌面从官网安装；Docker 使用 Debian Bookworm 软件包。原桌面工作流使用 Blender 4.5 系列，实际版本和导出接口需分别核对。 |
| [CadQuery](https://github.com/CadQuery/cadquery) | 参数化结构和 STEP 桥接 | 桌面在使用者虚拟环境中从 PyPI 安装；Docker 按 `deploy/cad-requirements.txt` 安装并记录解析后的包版本。 |
| [build123d](https://github.com/gumyr/build123d) | Text-to-CAD 的参数化几何能力 | 按 Text-to-CAD 原项目环境要求独立取得。 |
| [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp) | 历史原生发送研究来源 | 发布首版使用官方 Studio 路线。旧适配器依赖上游内部模块和本机补丁，保留来源链接供研究。 |
| [Hunyuan3D-Swift](https://github.com/ZimengXiong/Hunyuan3D-Swift) / [Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2) | 可选图生外形候选 | 代码和模型权重分别从原作者取得并检查许可。`image3d` 保留本地适配源码，需要自行准备对应隔离环境和来源清单。 |

## 许可归属

核对过的上游许可包括 Text-to-CAD 的 MIT、Bambu Studio 与 Bambuddy 的 AGPL-3.0，以及 bambu-printer-mcp 的 GPL-2.0。使用者应以自己下载版本的 LICENSE 为准。上游的许可与署名继续适用，PrintLab 的非商用许可只覆盖 PrintLab 原创部分。

Docker 安装器 `deploy/install-bambu-studio` 固定校验 AppImage SHA-256 `e0dfb6dafe0181b6bf8635bbb9a7b106f02f2343f8e41949f1d156b3f36241e5`，保留包内许可、署名和资源；来源记录保存在 `/opt/bambu-studio/PRINTLAB-SOURCE.json`。对应版本源码为 [v02.09.01.70 源码树](https://github.com/bambulab/BambuStudio/tree/v02.09.01.70) 和 [源码归档](https://github.com/bambulab/BambuStudio/archive/refs/tags/v02.09.01.70.tar.gz)，构建说明与依赖定义随该版本源码提供。PrintLab 未修改上游二进制。分发者仍需确保完整对应源码及包内其他组件的许可要求可满足，不能只保留二进制下载链接。镜像需要 Linux x86_64；更新版本时同时核对来源、SHA、运行库、官方资源与实际离线切片结果。

旧原生客户端通过进程内载入 `bambu-printer-mcp` 内部模块工作。首版发布包移除了该客户端、上游补丁和相关运行环境，保留本项目的历史任务读回与独立检查逻辑。首版 `bambu_native` 发送返回明确的不可用原因，官方 Studio 工作流在 `PRINTING.md` 中说明。

## 工作台软件依赖

Python 包在 `Harness/pyproject.toml` 声明。网页依赖在 `Harness/studio/package.json` 与 `package-lock.json` 声明，包括 React、Three.js、Lucide、react-markdown 与 Vite。桌面安装时由包管理器从 PyPI/npm 获取；Docker 构建时下载并将运行依赖、前端 bundle 及保留的许可文本放入镜像。基础镜像、apt 和范围版本的 Python 包以该镜像的软件清单与 SBOM 为准，不能从版本范围推定实际发布版本。

## 升级方法

先阅读原项目新版本说明，在独立目录安装，验证 CLI/MCP、模型尺寸、导出和设备只读接口，再更新本机配置。保存实际版本和验证结果，已确认的打印任务继续绑定其原文件和参数。
