# 图标获取与统一化调研（2026-09-08）

> 目标：解决三个现状痛点——① 不规则图标（圆形 logo、异形）提取后观感差；
> ② 大 APK（QQ/微信 >200MB）无图标；③ 预设 SVG / 哈希色 / 真实提取三来源混排不统一。
> 本文只做调研与方案推荐，不改代码。事实底账见 RESEARCH.md §2，视觉规范见 DESIGN.md §3.1。

## 0. 背景与现状（对照基线）

当前实现（`duo/core/apps.py`）：

- 获取：`pm path` 拉整包 APK（>200MB 跳过）→ 本地 aapt2 `dump badging` →
  自适应图层（fg/bg）按 512 画布合成、中心裁 341px（72/108）→
  圆角矩形 alpha 蒙版（半径 23%，4x 超采样）→ 缓存 `.r2.png`。
- 统一：预设 SVG（品牌色微渐变 squircle + 原创单字）、标签哈希色 fallback、
  真实提取图，三者统一套同一 squircle 蒙版。

问题：a) 圆形/异形 logo 直接套方圆角蒙版后四角露出白底或渐变断层；
b) 大 APK 直接放弃；c) 预设的"自绘单字"与真实 logo 的视觉密度、留白、色彩饱和度不一致。

---

## 1. OEM 图标统一化做法（Flyme / ColorOS / MIUI-HyperOS）

### 1.1 共同架构：官方重绘库（白名单）+ 遮罩兜底 + 主题包扩展

三家 OEM 殊途同归，都是**三层结构**：

| 层 | 内容 | 决定方 |
|---|---|---|
| 官方重绘 | 按使用量/重要性白名单收录的 top-N 应用（数千量级），按品牌视觉规范重绘 | OEM 内部编辑团队（+开发者申请通道） |
| 系统遮罩 | 未收录应用套统一形状遮罩（圆形/圆角矩形/squircle，部分系统可换） | 系统固定或用户选择 |
| 主题包 | 第三方图标包按 `包名 → 图标资源` 映射表整体替换 | 用户安装 |

- **魅族 Flyme**：为"几千个最常用的第三方应用"重绘图标（PRO 6 官方页）；
  重绘由应用商店编辑挑选"优质应用"，开发者也可发 512×512 PNG 到
  dev@meizu.com 申请重绘；系统提供"Flyme 风格图标"开关，关闭即恢复原始图标
  （[魅族开放平台 FAQ](https://open.flyme.cn/docs?id=110)、
  [PRO 6 页面](https://m.meizu.com/products/pro6/flyme)、
  [FAQ 转载](https://www.applebyme.store/Wap/Article/show/1811)）。
  → **决定重绘 vs 遮罩的判据是"重要性/品质"，纯人工白名单，无算法**。
- **OPPO ColorOS**：ColorOS 7"重绘了大量第三方图标"，同时向用户开放
  **自定义图标外廓形状、大小和比例**（含鹅卵石形选项）
  （[新浪科技 ColorOS 7 报道](https://tech.sina.cn/mobile/xp/2019-11-20/detail-iihnzhfz0520777.d.html?vt=4)、
  [ColorOS 图标自定义说明](https://www.coloros.com/instruction?id=654&version=ColorOS+11)）。
  主题商店的图标模块 = `res/` 素材 + `allApps.xml` 包名映射配置
  （[OPPO 开发者社区教程](https://open.oppomobile.com/bbs/forum.php?mod=viewthread&tid=2690)）——
  与开源社区的 CandyBar `appfilter.xml` 格式同构。
- **小米 MIUI/HyperOS**：
  - 早通道（MIUI 7 起）：开发者在小米开放平台提交"完美图标"——**上传未裁切的
    矩形 PNG（90/136/168/192px），系统用统一模板裁切保证形状整齐**
    （[完美图标提交教程](https://dev.mi.com/docs/appsmarket/distribution/perfect_icon/)、
    [完美图标设计规范](https://dev.mi.com/docs/appsmarket/technical_docs/perfect_icon_design/)）。
    注意其规范思路：**开发者只给"内容"，形状统一交给系统模板**——这正是
    adaptive icon 的前夜，也是 Duo 预设模板的正确心智模型。
  - MIUI 12+ 桌面"完美图标"特性 = 系统内置重绘图标 + 应用自带 adaptive icon，
    可分层、可去"牛皮癣"（营销角标）
    （[MIUI 完美图标补全计划](https://github.com/pzcn/Perfect-Icons-Completion-Project)）。
  - HyperOS：主题商店图标分类可整体换包，桌面编辑模式提供图标大小/圆角滑杆；
    HyperOS 隐藏了原生 Android 的图标形状选择
    （[果粉控教程](https://www.guofenkong.com/wz/654090.html)）。
  - 值得警醒：小米应用商店**计划停止完美图标服务**
    （[官方文档](https://dev.mi.com/console/doc/detail?pId=949)）——OEM 自建重绘库
    的维护成本高到连小米都在收缩，Duo 不应走自建品牌 logo 库的路。

### 1.2 社区补位：完美图标补全计划（对 Duo 最有参考价值）

MIUI 的分层/去角标只对"自带规范 adaptive icon"或"被官方重绘"的 app 生效，
社区项目 [Perfect-Icons-Completion-Project](https://github.com/pzcn/Perfect-Icons-Completion-Project)
补齐其余应用，其**适配规则精确列出了"遮罩解决不了什么"**，与 Duo 的痛点同构：

1. APP 自身没有 adaptive icon（静态图标效果）→ 必须重绘才能统一；
2. "适配后不完美"——**整个图标都是前景**（fg/bg 不分层，如百度地图前后景不分）
   → 遮罩后四角露底，观感差；
3. 适配后仍有牛皮癣（如联通营业厅的 5G 角标）→ 遮罩去不掉。

该项目与华为主题图标结构相同，且有贡献者"自动化绘制了 3000+ 图标"——
证明**不规则图标的根治只能靠"重绘/重排内容"，遮罩只对规范方形图有效**。

### 1.3 第三方桌面的做法：生成式统一（Nova "自动主题"）

Nova Launcher 支持 icon pack 的**自动生成模式**：对没有专属替换图的 app，
用主题包提供的 `background + foreground + scale + mask` 四要素**合成**一个
统一图标（[teslacoil/Example_NovaTheme](https://github.com/teslacoil/Example_NovaTheme)）。
这是"不自建重绘库也能统一"的工程答案：**把提取到的圆形/异形 logo 当作
foreground，放到一个统一背景板上**——OEM 的鹅卵石/OnePlus 风格"白底衬托"同源。

**小结**：OEM 靠"人工白名单重绘 + 遮罩兜底 + 用户可关"，社区靠众包补绘，
第三方桌面靠"内容→统一背景板"的生成式合成。Duo 作为投屏启动器没有重绘库，
应走 Nova 式**生成式统一**路线（详见 §6）。

---

## 2. 更好的获取通道（aapt2 之外）

### 2.1 候选通道逐项评估

| 通道 | 原理 | 可行性 | 结论 |
|---|---|---|---|
| A. `content query` launcher 数据库 | 查 Launcher3 `favorites` 表 | **不可行**。表里只有 intent/位置等列，**不存渲染后的图标位图**（[LauncherProvider 源码](https://android.googlesource.com/platform/packages/apps/Launcher3/+/refs/heads/main/src/com/android/launcher3/LauncherProvider.java)）；provider 需签名级权限 `com.android.launcher.permission.READ_SETTINGS`，shell 也常被拒（[SO: Trying to access the LauncherProvider](https://stackoverflow.com/questions/25060064/trying-to-access-the-launcherprovider)）；渲染后的图标缓存在 launcher 私有目录，需 root | 排除 |
| B. `dumpsys package` | 输出 applicationInfo 的 icon **资源 ID**（0x7f...） | 只给 ID 不给像素。解码仍需 resources.arsc + 资源文件（回到 §3 部分拉取问题）。可作 ID 快速校验，无独立价值 | 排除（作为独立通道） |
| C. 截屏 launcher 网格 + uiautomator 识别 | `adb exec-out screencap -p` + `uiautomator dump` 拿 bounds，切页采集 | 技术可行（[android-adb 技能文档](https://github.com/httprunner/skills/blob/main/android-adb/SKILL.md)）但工程上极脆弱：网格节点只有 label 无包名（对位难）、文件夹/小部件遮挡、需自动翻页、截图是 OEM 遮罩+阴影+壁纸邻接的成品（再套 Duo 蒙版会双重裁切）、分辨率/AA 不一致。**得到的是"OEM 渲染结果"而非可再加工素材** | 仅作最后手段 |
| D. 端侧 helper APK | 装一个小 APK 用 `PackageManager.getApplicationIcon` 批量导出 | 可行且有先例（[android-icon-label-exporter-apk](https://github.com/GeorgeEnglezos/android-icon-label-exporter-apk)，为 scrcpy GUI 导出图标）。缺点：要在用户手机上装 app，侵入性高，Duo 定位不符 | 备选 |
| E. **端侧 DEX（app_process）** | 推一个小 dex 到 `/data/local/tmp`，`CLASSPATH=... app_process` 以 shell uid 运行，调设备自己的 `PackageManager` 渲染 `AdaptiveIconDrawable` 成 PNG 回传 | **可行，先例充分**：[APIE](https://github.com/enigma550/APIE)（TypeScript CLI）即此方案——`on-device/icon_extractor.dex` 由 adb 推送，设备侧完成 adaptive 图标提取，支持 squircle/circle/rounded 蒙版、monochrome 层、`android:roundIcon` 偏好，输出 PNG/WebP/SVG。**不拉 APK、无体积上限、无 aapt2 依赖、密度/mipmap 解析由系统完成（永远选对资源）** | **首选** |

### 2.2 通道 E 详述（推荐主通道）

- 原理：`adb push icon_extractor.dex /data/local/tmp/` →
  `adb shell CLASSPATH=/data/local/tmp/icon_extractor.dex app_process / IconExtractor <pkg>` →
  stdout/base64 回传 PNG（或写 `/data/local/tmp` 再 `exec-out cat`，二进制安全见
  [SO: binary over adb](https://android.stackexchange.com/questions/213454/how-to-passthrough-a-file-from-pc-to-android-and-back-using-exec-in-and-exec-out-dire)）。
- 权限：shell uid（2000）可读 `/data/app/*.apk`（644）且可通过 binder 调
  PackageManager——APIE 证明整条链路无 root 可跑。
- 与 scrcpy 同构：scrcpy 的 server 也是 adb 推 jar + app_process 运行，
  用户已验证接受此模式。
- 收益：QQ/微信等 >200MB APK 的图标问题直接消失；坏点（`applicationInfo.icon`
  为 0、仅 VectorDrawable 的 fg）由系统解码器兜住，比我们手写解析鲁棒。
- 成本：需要预编译一个 ~10-20KB dex（Java 单文件，javac+d8 构建，可入库产物）；
  老 Android（<7.0 无 adaptive icon）退化为 legacy raster，仍可渲染。

---

## 3. 大 APK 图标直取（不拉整包）

ZIP/APK 的中央目录（central directory）在**文件尾部**，逐条目读取不需要整包。
HTTP 世界已有成熟先例：[python-remotezip](https://github.com/gtsystem/python-remotezip)、
[zipwire](https://pypi.org/project/zipwire/)（range 请求只拉中央目录+目标条目）。
adb 上没有 range 请求，但等价物存在：

1. **`dd` 区间读**：`adb exec-out dd if=/data/app/xxx.apk bs=4096 skip=N count=M`
   可读任意区间（[SO: dd 在 adb 脚本中的用法](https://android.stackexchange.com/questions/221085/command-has-different-output-when-running-a-shell-or-in-script)，
   注意必须用 `exec-out` 保证二进制不换行污染）。
   流程：先读 APK 尾部（~1MB）解析 EOCD + central directory → 本地算出
   `AndroidManifest.xml`、`resources.arsc`、icon 条目的偏移 → 逐条目 `dd` 拉回。
   icon + manifest + arsc 通常 <5MB，与 APK 总大小无关（500MB 游戏也一样）。
2. **设备侧 `unzip -p`**：较新的 Android 自带 toybox unzip，可直接
   `adb exec-out unzip -p /data/app/xxx.apk res/xxx.png` 单条目流式输出；
   条目名列表用 `unzip -l` 或上面的中央目录解析获得。设备不支持时退回 dd 方案。
3. **resources.arsc 的本地解析（aapt2 替代）**：纯 Python 生态已有现成解析器——
   [androguard](https://androguard.readthedocs.io/en/latest/intro/axml.html)
   （`androguard arsc`/`axml`，chunk 格式解析）与
   [pyaxmlparser](https://pypi.org/project/pyaxmlparser/)（`apk.icon_info` /
   `apk.icon_data` 直接出图标）。**更省事的路线：把拉回的 manifest+arsc+icon
   重打包成一个"子集 APK"，喂给现有 aapt2 流程**——zipfile 解析器只按需读条目，
   缺失的 classes.dex/lib 条目不影响 badging 与图标提取，**现有代码零改动**。

结论：>200MB 跳过应改为"整包拉取"→"尾部+条目拉取"；若走了通道 E 则此问题
整个消失（§3 是通道 E 不可用时的同构备胎，也是增量更新缓存时可复用的技巧）。

---

## 4. 内置常用图标库：合法性与来源

### 4.1 开源图标包

- 框架级：[Iconify](https://iconify.design/) 本体 MIT（旧包 Apache-2.0/GPL-2.0 双许可已统一为 MIT），
  但**图标集各自带许可证**（CC BY 4.0 / GPL / OFL 等，见
  [collections.md](https://github.com/iconify/icon-sets/blob/HEAD/collections.md)）——
  "Iconify 是 MIT"不等于"里面的图标可随意分发"，需要逐集核对。
  Iconify 的 Logos 集（1362 个品牌 logo，CC BY 4.0）含大量中国 app 图标，但见下。
- Android 图标包生态（CandyBar 系，如 Arcticons/Pixel 系）多为 GPL/CC BY，
  **内置分发需随附署名与许可文本**，且其内容是"重绘版"品牌图标，商标问题仍在。

### 4.2 品牌 logo 的版权/商标边界（关键先例）

- logo 可同时受**商标权**（更主要）与**著作权**（除非过于简单）保护
  （[law.SE: Is it legal to use icons from other companies](https://law.stackexchange.com/questions/68128/is-it-legal-to-use-icons-from-other-companies-in-my-app)）。
  "指名使用"（nominative fair use，为指代该产品而使用其商标）有抗辩空间
  （[Signa: Trademark Fair Use](https://signa.so/blog/trademark-fair-use-explained)），
  但**把品牌图标打包进安装包整体分发**超出了典型指名使用的范围。
- **决定性先例：Simple Icons（整体 CC0、3400+ 品牌 SVG）仍被品牌方法务要求下架**——
  Microsoft 法务通知后全量移除微软系图标
  （[issue #11236](https://github.com/simple-icons/simple-icons/issues/11236)、
  [PR #10019](https://github.com/simple-icons/simple-icons/pull/10019)），
  Adobe 随后同样被移除
  （[PR #10018](https://github.com/simple-icons/simple-icons/pull/10018)）。
  即：**"图标文件是 CC0"挡不住商标法；连专门做品牌图标的开源项目都守不住**。
- 对 Duo 的直接结论：
  1. **安装包内置真实品牌 logo 库：不做**（现行"品牌色+原创单字"是对的法律姿势）；
  2. 从**用户自己设备**提取的图标仅存本地缓存（不随安装包分发），与 scrcpy 等投屏
     工具同风险级，安全；
  3. 可选增强：支持**用户自选导入 icon pack**（解析 CandyBar `appfilter.xml` /
     ColorOS `allApps.xml` 映射）——素材是用户提供的，Duo 只做解析，与 Nova 等
     桌面同等地位；文档里注明 GPL/CC 包的署名义务归用户使用行为。
  4. 不建议走 Iconify 在线 API 拉 Logos 集：技术上可行（CC BY 4.0 可商用+署名），
     但商标风险同 4.2，且引入网络依赖，性价比低。

---

## 5. 自适应图层合成的最佳参数（官方 spec 对照）

官方规范（[AdaptiveIconDrawable API](https://developer.android.com/reference/android/graphics/drawable/AdaptiveIconDrawable)、
[Implement adaptive icons (AOSP)](https://source.android.com/docs/core/display/adaptive-icons)、
[Nick Butcher: Designing Adaptive Icons](https://medium.com/google-design/designing-adaptive-icons-515af294c783)、
[Compose 文档](https://developer.android.com/develop/ui/compose/system/icon_design_adaptive)）：

| 参数 | 官方值 | Duo 现值 | 偏差评估 |
|---|---|---|---|
| 图层画布 | 两层均 108×108dp | 512px 代表 108 单位 | ✅ 等价 |
| 可视视口 | 中央 72×72dp（蒙罩最大 72dp） | 裁 512×72/108=341px | ✅ 换算正确 |
| 安全区 | **中央 66dp 直径圆**（蒙罩为凸形、距中心最少 33dp）保证不被裁 | 未显式保证 | ⚠️ fg 内容越界是"圆形 logo 观感差"的来源之一：若 fg 把 logo 顶到 66dp 圈外、贴 72dp 边，方圆角蒙版必然切到内容 |
| 外圈留白 | 每边 18dp 供视差/阴影 | 合成时正确裁掉 | ✅ |
| 像素取整 | 108 的整数倍换算最干净 | 512/341 有取整误差（341.33） | 建议改 **432 画布 / 288 可视**（72/108 精确整数倍）或 324/216 |
| roundIcon | manifest 可另给 `android:roundIcon` | 未使用 | APIE 提供 `--round` 偏好；对 Duo 无必要（我们统一形状），但 legacy 路径遇到"仅 roundIcon 有透明版"时可参考 |
| monochrome | 108dp 单前景层，系统上色（themed icons） | 未使用 | 可作未来暗色/主题化增强 |

**对"圆形 logo 观感差"的正确归因**（结合 §1.2 MIUI 社区规则）：
不是 72/108 裁错了，而是**内容层（fg）本来的设计就预期被系统遮罩裁掉边缘**
（如全幅圆形 fg 在圆形蒙罩机型上完美，在 Duo 的 squircle 上就四角露底）。
OEM/社区的解法都是"重排内容"而不是"换蒙罩"。因此 Duo 的合成修正应当是：
fg 不按 108 全幅贴，而是**检测 fg 的实际内容包围盒（alpha bbox）**，
若内容接近满幅/圆形，则整体缩到 ~55-60% 再落到统一背景板上（见 §6）。

---

## 6. 推荐方案（按优先级，5 条）

### P0-1 获取通道改端侧渲染（app_process DEX），替代"拉整包 + 本地 aapt2"

内置一个 ~15KB 的 `icon_extractor.dex`（Java 单文件：`PackageManager.getApplicationIcon`
→ `AdaptiveIconDrawable` 按 432px 画布渲染 → PNG 写 stdout），
push 到 `/data/local/tmp` 后 `CLASSPATH=... app_process` 执行，Duo 侧 `exec-out` 收流。
**同时解决大 APK（QQ/微信/游戏）与资源解析鲁棒性两个问题**；拉整包 + aapt2
降级为 fallback（老设备 dex 跑不动/被禁时）。参考实现：[APIE](https://github.com/enigma550/APIE)。
（`pm path` + `stat` + 200MB 守卫与整包缓存逻辑可整体退役，图标缓存 key 改为
`包名@versionCode`。）

### P0-2 统一化策略从"套蒙罩"升级为"生成式统一"（Nova 式背景板）

三来源收敛为一条渲染管线，统一 squircle 底板 + 内容自适应排版：

1. **规范 adaptive 图**：fg/bg 按官方 108/72/66 规范合成（画布 432、可视 288）；
2. **不规则/圆形/满幅图**：alpha bbox 检测内容形状——近似圆/满幅（内容直径
   ≥ 画布 90%）时，不再硬套蒙罩，而是**缩至 ~56% 居中，落到从 bg/哈希色提取的
   统一色板 squircle 上**（即 Nova `background+foreground+scale+mask` 四要素合成，
   也是 OEM"白底衬托"的做法）；方形/规则图照旧直接蒙罩。
3. **未知应用**：保留现有预设单字模板（法律安全，见 §4.2），但将模板的
   饱和度/留白参数对齐真实图标的分布，减少混排突兀感。

这一条直接回应痛点 ①③：观感统一不再依赖"app 恰好是规范方形图"。

### P1-3 部分拉取（EOCD + 条目 dd/unzip -p）作为大 APK 的离线备胎

当端侧 DEX 不可用时：读 APK 尾部 1MB 解析中央目录 → 仅拉
`AndroidManifest.xml` + `resources.arsc` + icon 条目（通常 <5MB）→
本地重打包"子集 APK"喂现有 aapt2 流程（**现有解析代码零改动**），
或用 [pyaxmlparser](https://pypi.org/project/pyaxmlparser/)/[androguard](https://androguard.readthedocs.io/en/latest/intro/axml.html) 纯 Python 解析。
设备有 toybox unzip 时优先 `adb exec-out unzip -p` 单条目直出。

### P2-4 内置图标库：不做品牌库，改做"用户导入 icon pack"可选功能

- **不内置**任何真实品牌 logo（Simple Icons 被 MS/Adobe 法务下架的先例，§4.2）；
  预设单字模板维持原创。
- 增加"从本机导入图标包"：解析 CandyBar `appfilter.xml`
  （与 ColorOS `allApps.xml` 同构，包名→drawable 映射，见
  [Nova 主题规范](https://github.com/teslacoil/Example_NovaTheme)），
  素材由用户提供、仅本地缓存，Duo 地位等同第三方桌面。

### P2-5 合成参数与细节修正（小改）

- 画布 512/341 → **432/288**（108 的整数倍，消除取整误差；341.33 的非整数
  缩放在小尺寸显示时会累积锯齿）；
- fg 贴图前做 alpha bbox 检测（服务于 P0-2 的排版决策，也修"透明留白不均"）；
- 蒙版 4x 超采样保留；圆角 23% 维持（DESIGN.md §3.1 基准不动）；
- manifest `roundIcon` 不追（我们统一形状）；monochrome 层留作未来主题化。

### 实施顺序建议

P0-1（通道换血，风险集中在 dex 构建与回传协议，参考 APIE 可直接对照）→
P0-2（纯本地合成逻辑，PIL 已有）→ P1-3（仅当 P0-1 fallback 需求出现）→
P2-4/P2-5（体验增强）。P0-1+P0-2 合计可消灭痛点 ①②③ 的根因。

---

## 7. 参考链接汇总

**OEM 统一化**
- 魅族开放平台 FAQ（Flyme 图标/重绘申请）：https://open.flyme.cn/docs?id=110
- 魅族 PRO 6（"重绘了几千个第三方图标"）：https://m.meizu.com/products/pro6/flyme
- ColorOS 7 图标重绘报道：https://tech.sina.cn/mobile/xp/2019-11-20/detail-iihnzhfz0520777.d.html
- ColorOS 图标自定义：https://www.coloros.com/instruction?id=654&version=ColorOS+11
- OPPO 主题图标模块结构（allApps.xml）：https://open.oppomobile.com/bbs/forum.php?mod=viewthread&tid=2690
- 小米完美图标提交教程：https://dev.mi.com/docs/appsmarket/distribution/perfect_icon/
- 完美图标设计规范：https://dev.mi.com/docs/appsmarket/technical_docs/perfect_icon_design/
- 小米停止完美图标服务说明：https://dev.mi.com/console/doc/detail?pId=949
- MIUI 完美图标补全计划（适配三规则）：https://github.com/pzcn/Perfect-Icons-Completion-Project
- Nova 主题规范（background/foreground/scale/mask 生成式统一）：https://github.com/teslacoil/Example_NovaTheme

**获取通道**
- APIE（端侧 DEX 提取 adaptive 图标）：https://github.com/enigma550/APIE
- android-icon-label-exporter-apk（helper APK 方案，scrcpy 生态）：https://github.com/GeorgeEnglezos/android-icon-label-exporter-apk
- Launcher3 LauncherProvider 源码（favorites 表无图标）：https://android.googlesource.com/platform/packages/apps/Launcher3/+/refs/heads/main/src/com/android/launcher3/LauncherProvider.java
- LauncherProvider 权限问题：https://stackoverflow.com/questions/25060064/trying-to-access-the-launcherprovider
- adb screencap/uiautomator 采集：https://github.com/httprunner/skills/blob/main/android-adb/SKILL.md
- adb 二进制安全传输：https://android.stackexchange.com/questions/213454/

**部分读取 / 解析**
- python-remotezip（range + 中央目录）：https://github.com/gtsystem/python-remotezip
- zipwire：https://pypi.org/project/zipwire/
- pyaxmlparser（icon_info/icon_data）：https://pypi.org/project/pyaxmlparser/
- androguard arsc/axml 解析：https://androguard.readthedocs.io/en/latest/intro/axml.html
- toybox dd 在 adb 中的行为：https://android.stackexchange.com/questions/221085/

**自适应图标规范**
- AdaptiveIconDrawable（108/72/18dp 定义）：https://developer.android.com/reference/android/graphics/drawable/AdaptiveIconDrawable
- AOSP Implement adaptive icons（66dp 安全区、config_icon_mask）：https://source.android.com/docs/core/display/adaptive-icons
- Nick Butcher: Designing Adaptive Icons：https://medium.com/google-design/designing-adaptive-icons-515af294c783
- Compose 自适应图标设计（66x66 safe zone 图示）：https://developer.android.com/develop/ui/compose/system/icon_design_adaptive

**合法性与图标库**
- Iconify 许可结构（框架 MIT、图标集各自许可）：https://iconify.design/ 、https://github.com/iconify/icon-sets/blob/HEAD/collections.md
- Simple Icons 移除微软品牌（法务通知）：https://github.com/simple-icons/simple-icons/issues/11236
- Simple Icons 移除 Adobe：https://github.com/simple-icons/simple-icons/pull/10018
- Simple Icons 法律免责声明：https://github.com/simple-icons/simple-icons/blob/master/DISCLAIMER.md
- 品牌 logo 的商标/著作权问题：https://law.stackexchange.com/questions/68128/
- 商标指名使用：https://signa.so/blog/trademark-fair-use-explained
