# UI 优化摸底结论（2026-09-07）

> 任务起点的事实底账。决策与规范见 DESIGN.md；方案稿见 mockups/。

## 1. QQ 等应用找不到的根因

- 面板应用来源有二：**目录**（`APP_CATALOG`，仅 5 项：不背单词/哔哩哔哩/
  微信/WPS/微信读书，经全量 `pm list packages` 检查 installed 标志）与
  **第三方枚举**（`pm list packages -3`，只列第三方包）。
- 国产 ROM **预装的 QQ**（`com.tencent.mobileqq`，NT 版为 `com.tencent.qq`）
  在包管理器里属于系统侧，`-3` 不列它；目录里也没有 QQ → 网格永远不出现。
- 修复：目录扩到 ~25 常用应用（覆盖预装大户：QQ、淘宝、支付宝、抖音、
  网易云音乐、知乎、微博、京东、拼多多、小红书、高德、百度、美团、饿了么、
  腾讯/爱奇艺/优酷视频、酷安……），目录走全量存在性检查即可命中预装应用。
  第三方 `-3` 枚举维持（它与目录合并逻辑已正确）。

## 2. 图标现状

- 真实图标：APK 拉取 → aapt2 badging → 自适应图层合成 PNG（缓存于数据目录）。
  失败路径：无 PIL / APK > 200MB / badging 缺 icon。
- fallback：灰底圆 + 首字（`placeholderDisc`）——不精致，无品牌感。
- 无预设图标体系。Qt SVG 加载能力已验证（QImageReader 含 svg）。
- 预设图标策略见 DESIGN.md §3.1：品牌色微渐变 squircle + 白色原创单字，
  参数化 SVG 模板生成，不复制官方 logo。

## 3. 设置页过时项（待用户在方案稿确认）

| 项 | 判定 | 依据 |
|---|---|---|
| DPI 数字框 + 自动开关 | **删** | flex 会话自动取设备密度（bc30cb0）；面板启动路径不再用手动 DPI；CLI `--dpi` 保留 |
| 圆角三选一 + 滑块 + Canvas 预览 | **删** | 会话窗口圆角回归系统 DWM 默认（TODO 边界）；G2 实验入口撤出 UI，settings.json 手改仍生效 |
| 液态玻璃开关 | 留 | 性能逃生舱 |
| 引擎路径/FPS/码率/编码/音频/息屏 | 留 | 真实投屏质量项 |

## 4. 交互现状 → 目标

| 现状 | 目标 |
|---|---|
| 右键磁贴 = 直接切横竖屏（隐藏交互） | 右键弹上下文菜单：打开/置顶/横竖屏 |
| 置顶 = 排序置前（仍在网格里） | 置顶 = 独立固定栏（44px 小图标一行） |
| 无搜索 | 顶部搜索框，标签+拼音首字母即时过滤 |
| 排序 = 拼音首字母（已上线） | 保持；固定栏占位后网格纯字母序 |

## 5. 技术事实（实现要遵守）

- QML 网格是 QVariantList 整表替换模型（无增量 patch），一切批量改动必须
  收敛为单次 `appsChanged`（现有契约，`_sort_apps` 已遵守）。
- `ShaderEffectSource` 采样背景做玻璃，当前 `live:true` → 改 `live:false`
  （背景静态），软件后端自动降级。
- 拼音首字母工具（`label_sort_key`）在 `duo/core/apps.py`，搜索过滤复用，
  不另造轮子。
- 出图工具 `scripts/qml_shots.py`（offscreen）。
- 打包：PyInstaller onefile；新增 SVG 资源需进 spec（duo.spec 已有
  resources 收集，确认覆盖）。
