# Docker 内置 Bambu Studio

镜像构建时从 [Bambu Studio 官方发布](https://github.com/bambulab/BambuStudio/releases/tag/v02.09.01.70) 下载 Linux x86_64 Ubuntu 22 AppImage。版本固定为 `02.09.01.70`，SHA-256 固定为 `e0dfb6dafe0181b6bf8635bbb9a7b106f02f2343f8e41949f1d156b3f36241e5`。下载或校验失败会中止构建。

`install-bambu-studio` 校验后使用 `--appimage-extract` 解包到 `/opt/bambu-studio`，保留上游资源、许可和署名，并生成 `PRINTLAB-SOURCE.json` 来源记录。安装脚本也接受一个本地 AppImage 路径，复用下载缓存时仍执行相同 SHA 校验。

`bambu-studio.LICENSE` 为该版本[上游 LICENSE 原文](https://github.com/bambulab/BambuStudio/blob/v02.09.01.70/LICENSE)，SHA-256 为 `57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`，构建时复制到 `/opt/bambu-studio/LICENSE`。原作者与第三方组件的许可继续适用。

应用沿用非 root 的 `printlab` 用户。`/usr/local/bin/printlab-slicer` 为统一 CLI 入口，通过 `xvfb-run` 提供无显示器运行环境，并加载 AppImage 自带的 FFmpeg 库；不需要宿主 FUSE、桌面会话或 Bambu 网络插件。Debian Bookworm 中补齐 `libgtk-3-0`、`libwebkit2gtk-4.1-0` 运行依赖。

镜像默认设置：

```text
PRINTLAB_SLICER_PATH=/usr/local/bin/printlab-slicer
PRINTLAB_SLICER_PROFILES=/opt/bambu-studio/resources/profiles
```

固定版本包含 3798 个 profile JSON。A1、P1P 的 `0.4 nozzle` 机器、`0.20mm Standard` 工艺和 `Generic PLA`／`Generic PETG` 材料均在 `resources/profiles/BBL` 下；P1P 材料在其 `filament/P1P` 子目录。使用原配置的继承与模板，按目标打印机解析，保留来源哈希。

构建沿用项目原有基础镜像与代理参数。更新 Bambu Studio 时需同时更新版本、官方 URL、SHA，并在独立容器中验证 CLI、配置解析和离线模型切片；切片结果不能代替实体打印验收。
