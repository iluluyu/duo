# 图标获取与统一化调研（2026-09-08）

> 目标：解决三个现状痛点——① 不规则图标（圆形 logo、异形）提取后观感差；
> ② 大 APK（QQ/微信 >200MB）无图标；③ 预设 SVG / 哈希色 / 真实提取三来源混排不统一。
> 本文只做调研与方案推荐，不改代码。事实底账见 RESEARCH.md §2，视觉规范见 DESIGN.md §3.1。
> **实施进展（2026-09-13）见 §8。**

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

---

## 8. 实施记录（2026-09-13，P0-2 + adaptive 优先级）

真机渲染图 0913.png 定位两类真实提取图标的观感问题：酷安（透明底悬浮
绿色圆形 logo）与不背单词（白底遗留位图）在网格里与预设 squircle 混排
突兀。实施对应 §6 的 P0-2（生成式统一）+ 一处优先级修正：

### 8.1 adaptive 合成优先（duo/core/apps.py `extract_icon`）

旧逻辑对 `.xml` 图标引用**先取同资源的 legacy 光栅变体**（"most apps
still ship one"），adaptive 图层合成仅作兜底——酷安这类现代 app 的圆形
era 光栅抢占了规范 adaptive artwork，是圆形观感的根因。修正为：

- fg 光栅可解析 → 必走 adaptive 合成（432/288 官方几何，内容安全区居中）；
- fg 为 vector drawable（无光栅文件）且存在 legacy 光栅 → legacy 光栅（合成
  会丢 logo，不抢占）；bg-only 纯色板不作为抢占理由；
- 两者皆无 → 维持旧行为（bg-only 板或 None）。

### 8.2 光栅归一化 pass（`normalize_raster`，§6 P0-2 落地）

所有 legacy 光栅（含 adaptive 兑现失败的兜底）进入圆角蒙版前先过
归一化，两类失败形状修复，其余原样通过：

| 入参形状 | 判据 | 处理 |
|---|---|---|
| 满幅 artwork | 内容 bbox ≥ 两轴 96% 且 bbox 内覆盖率 ≥ 90% | 直通（只套圆角蒙版） |
| 透明底悬浮色块（酷安型） | bbox 内不透明覆盖率 ≥ 50%，主色非中性 | 裁内容 → 缩至画布 75% 居中落到主色 squircle 板（同色无缝） |
| 白底遗留位图（不背单词型） | 边框环 85% 同色且近白，内容非满幅 | 从边框泛洪背景 → 重涂为主色调 72% 向白渐变（pastel tint）；内容像素不动（内圈白色泛洪不到，保留） |
| 彩色实底位图 | 边框同色但非近白 | 直通（本就是品牌色板，authentic） |
| 线稿/异形低覆盖 | 覆盖率 < 50% | 直通（不猜背景，避免破坏） |
| 中性色主色（黑白 logo） | 主色近白/近黑 | 直通（无可用板色） |
| <48px 小图 | — | 直通（噪声不可信） |

参数：内容占比 0.75（落在规范 adaptive 前景典型区间 62-78%，agy Opus+glm 交叉评审）；
背景容差 = RGB 距离和 ≤60；近白 = luma ≥224 且 chroma ≤18；
tint = HSL(h, clamp(s×0.55, 0.16-0.38), L 0.87)——各色相 ΔE≈13-16 恒定存在感。缓存代 .r3 → .r5（alpha 乘法 + adaptive 白底重涂 + 75% 占比，旧缓存自然作废）。

主色提取：4-bit 桶直方图，跳过 alpha<128 与中性色像素，取众数桶均值。
白底重涂只改泛洪到的背景像素，内圈白色（logo 里的白环）不受影响。

### 8.3 刻意不做

- adaptive 合成结果不再过 normalize（内容已按规范排布，二次处理会过度）；
- 中性/线稿不猜背景（宁可保留 authentic，不引入哈希色板耦合）；
- P0-1（端侧 DEX）、P1-3（部分拉取）、P2-4（icon pack 导入）本次不动。

### 8.4 真机复测后的两处补充修复（2026-09-13 下午）

真机首轮部署后像素解剖（酷安/不背单词的 r3 vs r4 缓存）发现两个新事实：

1. **圆角蒙版的 putalpha 直接覆盖源 alpha**：透明底光栅（酷安 legacy
   res/o-_.png 即透明底绿圆）被抹成不透明黑底——0913.png 酷安黑块的
   真正根因不是"圆形异形"而是黑底。修复：蒙版改为与源 alpha 通道
   相乘（`ImageChops.multiply`），透明内容在直通路径也保真。
2. **adaptive 路径的白底 bg 层**：不背单词的 adaptive bg 是纯白图层，
   优先走合成后白底问题原样保留（换了个尺寸）。修复：
   `_compose_adaptive` 在裁切前过 `_recolor_white_bg`（与光栅路径共用
   的泛洪重涂）；彩色 bg 层不受影响（近白门控自然放行直通）。

另：blob 内容占比按 agy Opus + glm 双评审从 68% 调至 75%（落在规范
adaptive 前景典型区间 62-78%）；白底 tint 从 RGB lighten(0.72) 改为
HSL 定向调色（保色调，各色相存在感恒定）。

## 9. 设备端渲染（2026-09-13，P0-1 完成）+ G2 精致化

### 9.1 为什么回到设备端

§8 的 aapt2 管线修好了图标质量，但速度与覆盖留下两类残局：
(1) 114 个三方应用全量首扫要分钟级（拉整包 APK + 多轮 dump，酷安
116MB）；(2) inset 嵌套引用（Office 系）与部分 layer-list bg 仍解不出，
残留 4-10 个白板。社区成熟方案（scrcpy server、APIE）证明另一条路：
**把一个小 DEX 推到 /data/local/tmp，app_process 起 Java 进程，让系统
自己的 PackageManager 渲染图标**——adaptive 图层、inset、渐变、混淆资源
全部由系统原生处理，桌面看到什么我们拿到什么。

### 9.2 DuoIconRenderer（duo/resources/duo_icon_renderer.java → duo_icons.dex）

- 编译：javac（android-34 android.jar）→ D8 --release，5KB dex，
  源码与产物一并入库（duo.spec datas 打包）。
- 关键坑 1：`pm.getApplicationIcon(pkg)` 在 ColorOS 上对所有包返回
  默认 adaptive 图标甚至抛 SecurityException——必须用
  `pm.getResourcesForApplication(pkg).getDrawableForDensity(info.icon,
  densityDpi, theme)` 手动加载（顺带按屏幕密度取最清晰变体）。
- 关键坑 2：`ActivityThread.systemMain()` 前必须
  `Looper.prepareMainLooper()`，否则 PackageManager binder 回调创建
  handler 时崩溃。
- 关键坑 3：adb exec-out 的 stdin 转发不可靠（BufferedReader 永久
  阻塞）——包列表走设备端文件（push pkgs.txt，argv 传路径）。
- 输出：每包 `<pkg>.png`（adaptive = 432px 全 108 单位 artwork；
  legacy = 固有尺寸、密度最优、上限 576px 平方画布居中）+
  labels.txt（pkg/kind/versionCode/versionName/label，制表符分隔）。

### 9.3 PC 侧后处理（apps.py）

- `render_device_icons(adb, packages)`：一次 app_process 跑完全部包
  （114 包 4.0s），单次 pull 回传，逐包后处理入缓存 `.r11.png` +
  `device_meta.json`；`app_info` 先查该缓存，miss 才走 APK+aapt2
  fallback。控制器在 _load_all_apps 里对三方包 ∪ 已安装目录包批量
  预取，113 个图标含面板启动共 ~31s（旧管线分钟级）。
- adaptive：432 裁 72/108 可视中心 → `_recolor_white_bg` → G2 蒙版；
  **若裁切结果仍有透明**（ColorOS 上 Telegram 的 adaptive bg 层就是
  透明、依赖 OEM 蒙版成圆），转 `normalize_raster` 垫板。
- legacy：LANCZOS 统一重采样到 288（此前 67-1208px 共 20 种固有尺寸
  上屏，小图发虚大图锯齿——"缩放有点问题"的根因）→
  `normalize_raster` → G2 蒙版。

### 9.4 G2 连续曲率圆角（本轮"精致化"）

圆弧圆角在直边交点曲率 0→1/r 突变，60px 下有"切角感"。改为
`g2_outline`：角部 r×r 盒内超椭圆 |u/r|^n+|v/r|^n=1（n=5），与直边
零曲率衔接。三面同构：PNG 蒙版（4x 超采样多边形 + LANCZOS 回缩，
<4 的 alpha 残渣吸附 0）；预设 SVG 模板（v3，同函数出 path）；QML
fallback（JS 复刻同公式，SVG data-URI 渲染）。注意四角参数化中 TR/BL
需 swap cos/sin 项，方向反了会切掉直边（首个实现的坑）。

### 9.5 白底/透明角兜底链（glm 三轮验收驱动）

- 边框白家族 ≥40% 即以其均值为 flood 基色（OPPO 商城白底+绿带：
  模态边色是绿带，旧行为直接放弃）。
- 透明角图标的三个 passthrough 出口（满版白底夸克/低覆盖 OTA 线稿/
  中性内容日历）一律先白底化再走 `_recolor_white_bg`；彩色满版不受
  影响（flood 只吃白）。
- 纯中性内容 → 冷银板 (217,221,227)（HSL 215°/0.16/0.87）；
  flood 后全白无信息图整版涂银板。
- 真机终验：113 个图标 0 白板 0 透明角；glm 十二宫放大验收 Telegram
  破洞/OPPO 白板修复确认，G2 圆角 PASS。

## 10. 第四轮：圆角 30% + 内容尺度统一 + 暗板（2026-09-13）

用户反馈三项，对应三个修复：

1. **圆角太小**：G2 角延展 23% → **30%**（60px 格 r=18），更接近
   ColorOS/HyperOS 桌面的圆润观感。三处同步：蒙版默认值、预设 SVG
   模板（v4，r=18/60）、QML fallback（JS 0.30）。

2. **bilibili/Flexcil/Google "明显放大"**：根因是各应用 adaptive fg
   在 artwork 内的摆位不一（可视区 0.55-0.78 都有），并排忽大忽小。
   根治：dex 对 adaptive 额外输出 **fg/bg 两个分层渲染 PNG**（
   `AdaptiveIconDrawable.getForeground()/getBackground()` 各画 432），
   PC 侧 `_compose_layers` 用 fg alpha bbox（真实造型轮廓，扁平合成
   图里不可得）把前景归一到统一视觉带：span>0.72 缩到 0.66、
   span<0.52 放大到 ≤0.56（限幅 1.2x 防糊），bg 层近白则 tint。
   Google/Gemini 的纯白 bg 检测不到主色 → 冷银板（四色 G 在银板上
   对比反而更好）。

3. **计算器（暗色）角部填充**：ColorOS 计算器是满宽深灰蓝圆
   legacy，透明角在 G2 切边处露底。`normalize_raster` 新增满宽造型
   扩板：bbox span≥0.95 且 mean 不透明色 luma<0.45（暗造型）→ 以
   **造型原色**垫满画布（计算器 (63,67,78) 深板，暗色身份保留）。
   亮造型（白底满版）不适用此分支——alpha_composite 的 over 语义下
   不透明白底会盖住任何板色（夸克回归白板的教训），必须落回 flood
   重涂链。缓存代 .r12。

真机终验：113 图标 0 白板；bilibili/Flexcil/Google/酷安/Telegram 前景
span 全部落 0.64-0.66 带；计算器深板成型。

## 11. 第五轮收敛：圆角 38% + 分层合成的三个坑（2026-09-13）

- **圆角 30%→38%**：glm 像素测量发现 G2 的视觉切入带 ≈ 0.44×角延展
  （渐进切点的数学特性），30% 只等效圆弧 ~13%，仍偏方。上到 38%
  （288px 图 r=110，边缘实心起点 x≈63=22%）后视觉等效 ≈ iOS/ColorOS
  带宽。预设 v5（r=23/60）、QML fallback 0.38 同步。
- **分层合成的三个坑**（本轮流出的 r13 黑心事件）：
  1. Telegram 的 fg 层带一块黑色装饰小样（span 0.50），触发"小前景
     放大"分支后放大成黑块——**删除放大分支**，fg 在带内（≤0.72）
     一律原样贴（与系统桌面一致）；只保留 >0.72 的缩小归一。
  2. `_resize_over` 的预乘 backdrop 原来传黑色，fg 缩放后透明边缘烧黑
     ——backdrop 必须传 plate 主色。
  3. Telegram 的图层分配与直觉相反：可见内容在 bg 层（白盘+蓝圆 0.7
     宽），fg 几乎全空。`_recolor_white_bg` 增加圆盘判定：宽高比
     0.9-1.1、coverage 0.60-0.90（≈π/4）、直径 ≥65% 的圆盘内容用
     **原色融合**（满版品牌色，Telegram/夸克蓝环），带状/满版白底
     （OPPO 绿带、日历黑字）保持 pastel tint。缓存代 .r14。

真机：113 图标 0 白板 0 黑块；Telegram 满版蓝、夸克原色蓝板、计算器
深板、bili/Google/Flexcil 内容 0.64-0.66 带全部就位。

## 12. 第六轮：Apple 纯超椭圆 + 边缘三修（2026-09-13）

用户四点反馈（圆角再加大参考 Apple、边缘锯齿、白线、bilibili 色差），
社区调研（squircle.js / iOS 超椭圆文献）确认 iOS 图标 = 纯五次超椭圆
无直边，非"直边+圆角"结构：

- **形状**：`apply_rounded_mask` 默认 r=50% —— g2_outline 的四段角
  曲线在边中点相接、直边长度归零，形状退化为完整超椭圆，与 Apple
  数学同构。轮廓采样 40→96 步/角、蒙版超采样 4x→6x。
- **显示端锯齿**：288px 缓存缩到 60px 显示（150% DPI 下 90px）是
  ~3-5x 缩小，Qt 默认滤波产生摩尔纹/锯齿——QML Image 加
  `mipmap: true` + `smooth: true`（社区标准解）。
- **白圈**（夸克/Telegram logo 边缘白环）：flood 白重涂的边界白 AA
  像素残留——替换循环前对 flood mask 做 1px 8 邻域膨胀
  （MaxFilter(3)），膨胀区内仍近白的像素一并重涂。
- **bilibili 色差**：fg 层自带与 bg 同色的底块（粉 TV 脸叠粉渐变
  bg，两块粉色调不一产生突变）。`_strip_ground_tone`：fg 不透明像素
  中与 plate 中心色接近（diff L<28）的占比 ≥40% 时整块透明化，露出
  bg 渐变 → 色调连续；Google/Word 等 fg 与 bg 不同色不受影响。
  剥离后 fg bbox 重算，span 归一自然适配。缓存代 .r15、预设 v6
  （r=30/60）。

真机：113 图标 0 白板；Telegram/夸克白圈消除（径向采样连续）；
bili 行扫描为连续渐变。

## 13. 第七轮：定向边缘收锐 `_crisp_edges`（2026-09-13）

glm 第六轮放大审出残留白圈：**小尺寸 legacy 放大引入的宽 AA 带**——
夸克 107px 原图 LANCZOS 放大到 288 时，logo 边缘的白↔板过渡带同步
放大到 2-3px，flood 的 1px 膨胀盖不住。60px 显示态径向亮度剖面无
亮峰（人眼不可见），但 288 缓存放大态可见。

`_crisp_edges`（所有 G2 出图前统一过）：大窗口中值滤波（Median 7）
近似"板色场"（细内容在中值中消失）→ 内容 mask（与场差 >16）→ 边
缘带（内容收缩 2px）→ 带内亮于场 8+ 的像素重涂场色。夸克环采样
全为板色 (11-17,82-85,255)，Telegram 环偏差 ≤6/通道；0.02s/图。
缓存代 .r16。

## 14. 第八轮：白底忠实策略 + 六图标个案（2026-09-13）

用户反馈调性反转："夸克这种应该是白底，现在的图标都不够显著了"——
统一重涂体系让应用失去辨识度。定调：**官方忠实优先，生成式只兜底**。

个案根因与修复：
- **高德**（半涂银蓝板+绿边）：`_solid_border_color` 白家族判定对
  渐变底误判 → 加渐变排除（边框采样通道极差 >40 即非均匀底，直通）。
- **优酷**（品牌大圆被缩+银板）：fg 满宽（span≥0.95）是设计本身，
  不再缩放；bg 白层按新策略忠实保留。
- **夸克/日历/OTA**：白底直通（品牌白），仅 flatten 透明角；全白
  无内容才银板兜底（113 个中 1 个边缘 case）。
- **Flexcil 色阶**：`_crisp_edges` 的 Median(7) 场在渐变板上
  posterise 成块 → 场改为"板区保真 + 内容区 GaussianBlur(7) 填充"
  （薄内容从场中消失但渐变无损）。
- **邮件/小红书圆角瑕疵**：满宽渐变圆用均值色扩板产生色调接缝 →
  `_edge_ring_color`（alpha 边缘 3px 环带均值）扩板，渐变圆无缝。
- **Telegram**：随白底忠实回归官方白圈蓝圆（bg 层本就是白盘+蓝圆）。

`_recolor_white_bg` 大幅简化（白底直通+银板兜底），disc/tint 逻辑
退役；`normalize_raster` 的暗造型扩板、透明 blob 板保留。缓存代
.r17。113 图标 37s 重生成，1 个全白边缘 case（sangfor 淡 logo）。

## 15. 第九轮：白边/灰点/色阶/乱码 + 全量包 + 固定快捷方式（2026-09-13）

- **天气/主题商店白边**：两案不同根因。天气是圆角方形蓝图标（coverage
  0.95 → full_bleed → flatten **白**补角 → 角部白弧）——`_flatten_white`
  改为**边框模态色补角**（蓝图标补蓝、白图标补白，近黑模态=透明代理
  时回退内容均值）+ 环带白线重涂（`_repaint_ring_whites`：透明边界
  3px 内的近白实心像素涂板色，Android 渲染自带的 1px 白描边线）。
  主题商店是**系统应用不在 -3 批渲染**（无缓存 → fallback 字母板）——
  Adb.all_packages() 全量批渲染，113 → 289 个图标，系统应用首次获得
  真图标。
- **夸克角部灰点**：107px 原图透明角的 RGB 从黑跳白，resize 放大后
  flatten 混灰——`_defringe_to`（半透明像素 RGB 重涂目标色）在
  flatten/扩板前执行。
- **Flexcil 色阶**：`_crisp_edges` 的修复对大内容边缘（F 字）在渐变
  板上留场色台阶——双重门限：fix 面积 >0.4% 且非边缘环带（>50%
  距画布边 12.5% 内）才跳过。
- **Sam Helper 乱码 / 不背单词橙块 / ChatGPT 黑块**（同一根因三面）：
  bg 层纯白无墨时曾用「fg 均值色当板」——浅蓝 fg（Sam Helper）恰好
  成立，但橙色印章（不背单词）把整格吞成橙块、黑色结（ChatGPT）
  吞成黑板。终版语义：**bg 无墨时白就是官方底**（印章/黑结/浅蓝
  块都属 fg 内容，原位保留、照常参与内容带归一），plate 直接恢复
  纯白；仅 bg 全透明（Telegram 类）保留 fg 满位豁免。FlClash 靛蓝
  块同源，随终版一并修复。
- **固定=快捷方式**：`apps` 模型保留置顶条目（与 pinnedApps 共享
  同一 dict，icon patch 双模型一次生效）；togglePin 改为
  `_add_shortcut`/`_remove_from`。glm 曾报「bilibili 未回插网格」，
  放宽色阈值的像素聚类确认网格 (660,780) 处 27 命中簇即 bilibili
  磁贴——目测误报；pytest 全链路 + 真机像素双重确认。缓存代 .r18。

458 tests + ruff + mypy 全绿；全量 sweep 81s（289 图标）。

## 16. 第十轮：EasyTier 蓝板吞没与板色选择总规则（2026-09-13）

- **症状**：easytier-gui 显示为纯蓝块无图形。原图=透明底+亮蓝
  网状线条（coverage 0.35、mean=线条色）。
- **根因链**：coverage<0.5 → `_flatten_white` 补角；透明边框模态=
  透明黑 → r18 引入的「内容均值回退」把**线条色当板色** → 蓝板+
  蓝线=吞没。同设计曾让白圆浮透明（r18 前黑板）受益——均值回退
  对「内容=块板」成立、对「内容=线条」是灾难。
- **板色选择总规则**（收敛，用户点名理清）：
  1. **不透明边框的模态色**可直接补角（夸克白、天气蓝圆角方）；
  2. 透明边框时，**均值回退仅当不透明占比 ≥0.70**（均值=板，天
     气 0.95/优酷满版）；稀疏内容（<0.70）补角一律**白**——任何
     单色内容贴同色板都会被吞（EasyTier 蓝、假想黑圆同理）；
  3. dominant 当板前提：内容**非单色**或**实心**（不透明占 bbox
     ≥0.55，酷安绿圆白核 0.785）；**稀疏镂空的单色线条艺术**
     （`_is_monochrome_art`）用白板（EasyTier/ChatGPT 类线条）。
  一句话：**板与内容必须有区分度；拿不准时白**（与白底忠实定调
  一致）。
- 新增 `test_sparse_line_art_keeps_white_not_content_plate` 锁行
  为；`test_normalize_floating_blob_lands_on_same_color_plate`
  （酷安实心圆）回归通过。缓存代 .r20。

## 17. 第十一轮：图标加载性能（2026-09-13，用户：「还能再快一点」）

### 瓶颈测量
- 单图后处理 ~55ms（compose 26 + crisp 18 + G2 13），289 图标串行
  ≈16s——不是大头；
- **真正的大头是架构**：每次启动 `render_device_icons` 全量重跑
  （dex 渲染 289 包 ~12-15s + 目录 pull 289×3 文件 ~30s + 全量后处
  理 16s ≈ 86s），且缓存命中的图标也被压在 render 之后才亮。

### 优化（三层，全部落地）
1. **缓存秒亮（pass 1）**：`app_info(..., cache_only=True,
   device_meta=...)` 只读缓存（meta 一次读入避免 289 次 IO），miss
   直接跳过（不碰 APK fallback 慢路径）——网格在 ADB 枚举完成后
   毫秒级出全部已缓存图标；
2. **增量渲染（pass 2）**：dex 仍全量跑（12s，后台无感知），但只
   pull `labels.txt` 对比 versionCode——**pending（新装/升级/缺缓存）
   为空时零文件传输零后处理**；>30 个才目录整体 pull，少量时逐文
   件 pull；meta 只合并 pending 项；
3. **并行后处理**：`ThreadPoolExecutor(8)`（PIL C 内核释放 GIL），
  bump 缓存代的全量重生成 16s → ~3s。

### Rust 调研结论（用户问询，已答复在案）
单图 55ms 的主体是 PIL 的 C 实现（LANCZOS/高斯/合成）；Python 层
仅调度。瓶颈在「无增量」的架构而非语言——增量后日常启动零后处
理，Rust 重写无收益空间（设备端 dex+adb 传输 ~12s 是固有开销，
换语言不变）。不引入 Rust，避免双构建链复杂度。

### 体验（QML）
真图标到达时从 fallback 字母板 **180ms 交叉淡入**（opacity
Behavior 替代硬切换）。

### 实测
冷启动（缓存全热）：真图标出现 86s → **t≈4s**（彩色像素曲线
t4/t8/t14/t22/t34 = 6679/6589/6798/6828/6839，8s 处低谷为淡入
中间态）。`test_render_device_icons_incremental_skips_warm_cache`
锁增量契约。缓存代维持 .r20（无管线改动）。
