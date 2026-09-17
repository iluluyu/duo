# plan/todo.md — Python → Rust 全量迁移计划（2026-09-16 起）

> 本文件是**长期任务的唯一进度真相**：每完成一小节就回填对应复选框与
> 日期；发现新依赖/新结论也写回这里，防止跨会话错乱。历史路线存档见
> 仓库根 `TODO.md`（0.x 里程碑），本计划接管其后全部工作。

## 总则（执行纪律）

1. **像素移植，不做语义近似**：QML（`src/pyduo/ui/qml/*.qml`）与
   `docs/ui/DESIGN.md` 是唯一视觉规格。所有 px/圆角/颜色/字号/间距
   逐字面照抄，不发明数值。Qt `font.pixelSize` ≈ egui 字号直接用。
2. **逐模块移植**：`src/pyduo` ↔ `src/rustduo` 同名对照阅读后再写；
   语义先查文档（`docs/`、QML 注释、`docs/ui/DESIGN.md`），查不到
   才看 controller.py 行为，**禁止瞎猜**。
3. **可读性优先于进度**：对齐 Python 侧的函数式分层（纯函数 +
   数据结构 + 薄编排层）；渲染层文件按页面分文件，单文件 ≤ ~500 行；
   逻辑住 model/sessions/prefs/backend（已成立），app.rs 只做绘制转发。
4. **WSLg 本地对拍循环**：Linux 原生构建 duo-panel 在 WSLg 跑 +
   X11 截图，对照 `docs/validation/assets/qml-*.png` 基线
   （`scripts/qml_shots.py` 生成）；差异用 GLM-5.3-flash（视觉，
   zai MCP）评审，迭代到合格再上 Windows 真机。
5. **门禁不变**：cargo test/clippy/fmt + pytest/ruff/mypy(`duo`→现
   `pyduo`) + `scripts/parity_check.sh` 315/315 全绿才允许回填。
6. **提交粒度**：一个阶段一个 commit；回填本文件与代码同 commit。

## 目标架构

```
duo/                       # 仓库根（不变）
├── src/
│   ├── pyduo/             # Python 参考栈（原 duo/ 包，保读改删禁）
│   │   ├── core/          # engine/aspects/apps/… 纯逻辑 + 进程层
│   │   ├── ui/            # controller.py + qml/
│   │   └── resources/     # duo_icons.dex / chrome_overlay.cs
│   └── rustduo/           # Rust 实现（原 rust/ workspace）
│       └── crates/
│           ├── duo-core/  # 纯逻辑 + CLI（已完成，315/315 对齐）
│           └── duo-panel/ # egui 面板（本计划主体）
├── plan/todo.md           # 本文件
├── docs/                  # 设计/验收文档（DESIGN.md 为 UI 合同）
├── scripts/               # qml_shots.py / install-windows.ps1 …
└── tests/                 # pytest（import pyduo.*）
```

运行期目录（`~/.local/share/duo`、`%LOCALAPPDATA%\Duo`）与 exe 名
（duo-core.exe / Duo.exe）**不变**。

## 阶段计划

### P0 目录重组（✅ 2026-09-16）

- [x] git mv `duo` → `src/pyduo`；`rust` → `src/rustduo`
- [x] pyproject：src 布局 + 包名 pyduo + script 入口；tests/scripts
      import 批量改 `pyduo.*`（含 monkeypatch 字符串目标）
- [x] duo.spec / qml_shots.py / parity_check.sh / build_wsl.sh /
      build_windows.ps1 / install-windows.ps1 / accept_windows.ps1 /
      CI / app.py `_bundled_icon` parents 层级 / TODO.md 路径修正
- [x] sweep.rs `include_bytes!` 与 duo-panel build.rs 图标新相对路径
- [x] 全套件绿：pytest 488 / ruff / mypy(src/pyduo) / cargo 247 /
      parity 315；交叉构建 + exe 图标完好；qml_shots 基线重出

### P1 渲染基建（✅ 2026-09-16）

- [x] fonts.rs：Segoe UI + CJK 双字体栈 + "duo-bold" 粗体 family
      （QML DemiBold 档）；Linux 回退 Noto CJK
- [x] theme.rs：全量令牌 + SPOTS 几何 + FALLBACK_PALETTE 12 色 +
      fallback_color；rgba 改四舍五入对齐 QML #hex ARGB（0.72→184 非 183）
- [x] paint.rs：card/rounded_fill/dot/text_centered/text_left/
      canvas_spots/g2_squircle（duo-core g2_outline）/elide_6/x_mark/
      magnifier/speaker（QML Canvas 直译）
- [x] 图标渲染决策：不建自有缓存——egui Context 纹理管理器按 URI
      自带缓存（Image::from_uri）；fallback = g2_squircle + 首字白字
- [x] 画布色斑：SPOTS_BLUE/GREEN 常量 + paint::canvas_spots
- [x] 单测：令牌对拍（暗/亮 control/search/menu fill）、fallback
      确定性、elide 6 字规则

### P2 主面板骨架像素移植（✅ 2026-09-17 完成，GLM 双主题对拍通过）

- [x] 顶栏胶囊：y16 h32 通栏、flyoutFill r16 + cardBorder、分段
      内缩 2、选中不透明段 r14（暗 #48484A）、13px 选中 DemiBold
- [x] 设备卡：y64 h76、Dot 8+1 白环（在线绿/有设备琥珀/无设备灰）、
      15px DemiBold 状态 + 12px ink2 serial，左缘 14 间距 10
- [x] 固定应用卡：h68、Flow x12 y12 间距 12、44px 图标 r10 洗色
- [x] 镜像卡：h64、"设备镜像" 15px@左缘 12（DemiBold）、扬声器矢量
      14px@x78、音量条（4px 轨 hairline + accent 填充 + 12px 拇指、
      未知中性态、200ms 防抖）、"投屏" accent 68×32 r16 右缘 12、
      禁用 40%
- [x] 搜索胶囊：h36 r18、searchFill↔聚焦 flyoutFill、放大镜 16px
      @12、TextField 13px、清空钮 28×28
- [x] 应用网格：cellW = w/max(2,floor(w/92))、cellH 102、磁贴 60px
      图标 r14 洗色 + 标签 12px@76、6 字 elide、未装 40%、空态/
      无匹配文案与刷新按钮、网格区内部滚动（with_clip_rect 裁剪，
      tile 内 painter 全部走 clip——widget 式 ui.put 不吃 clip 是实测坑）
- [x] 网格语义跟齐 pyduo 2026-09 真机反馈：只显示已装（未装目录项
      不铺灰块）；第三方随 installed 集合进出
- [x] 运行卡：bottom 56、芯片 h32 r16、Flow 间距 8（无会话时网格
      延伸到页面底-40，与 QML chipsZone.visible 三项式一致）
- [x] Toast：bottom 16 h36 r18 pillFill、13px 白字、2.5s 淡出、
      启动「就绪」初始态（QML _status_text 对译）
- [x] 单测：布局 y 链计算 38 项（胶囊→设备→固定→镜像→搜索→网格，
      有/无 pin × 有/无芯片四象限）

**渲染管线关键发现（docs/validation 标定实测）**
- egui 0.31 Windows 着色管线 alpha 混合非线性病态（有效 alpha =
  a^0.75 级）→ theme.rs 所有静态半透明层 CPU 预合成不透明色
  （theme::over = QML sRGB 直混数学等价），GPU 只画不透明矩形+文字
- 增量重绘不擦除 → 半透明逐帧叠加饱和 → 色斑预合成不透明三层
- usvg 无系统字体 → preset SVG v7 去掉 <text>（模板版本 6→7），
  白字/glyph_ink 深字由面板层叠画（视觉与 Python 版等价；
  parity 比对两端剥 <text> 后逐字节一致）
- egui_extras 需 file+image+svg 三特性；Windows 路径须
  file:/// 正斜杠 URI
- 图标加载 Ready 门控失败走 G2 squircle 兜底（杜绝坏图三角）

**截图回路（P2 起常备）**
- Windows 侧桩：%USERPROFILE%\.local\share\duo\tools\duo-core-stub.{bat,py}
  （watch=单设备在线、apps=27 目录全装中文标签、其余静默 rc0，
  与 scripts/qml_shots.py 基线同状态）
- 命令模板：cd tools 目录；env DUO_CORE_BIN=stub.bat
  DUO_DATA_DIR=<临时隔离目录> WSLENV='DUO_CORE_BIN:DUO_DATA_DIR'
  （冒号分隔！空格分隔整串失效——真机偏好会漏进来）
  duo-panel.exe --shot <out.png> --page home|settings --theme light|dark
- 归一化 630×990→525×825（PIL LANCZOS）后与 qml 基线同坐标系
  （逻辑×1.25）像素采样对拍
- GLM-5.3-flash 终验：light/dark 双主题主面板宣布像素级一致 ✅

### P3 交互语义移植（✅ 2026-09-17 完成，语义对齐 controller.py）

- [x] 点击路由：磁贴/固定卡 = startSession（运行中 →
      startAppOnDisplay 拉回虚拟屏）；右键 = 上下文菜单
- [x] 应用右键菜单全量（QML appContextMenu 结构逐行）：打开 / 置顶
      到固定栏 / hairline / 自适应窗口（勾选行+关菜单）/ 固定比例 ▸
      （小节头 横屏 21:9·16:9·4:3·1:1·机身 / 竖屏 3:4·2:3·5:7·9:16·
      机身，圆点=fixed 记忆）/ 窗口栏 ▸（上巴 跟随默认·沉浸·系统；
      下巴 跟随默认·沉浸·系统·不显示，圆点=explicit）/ 音频独占（勾选
      **不收菜单**）/ 断开保留画面（勾选不收菜单）/ DPI ▸（跟随默认·
      160·240·320·自定义 −/+ 步进 10 键入 120–640）/ 渲染倍率 ▸
      （跟随默认·1×·1.4×·2×·3×·微调 0.1）
- [x] set_display_fixed 校验对齐 pyduo：冻结表 id 直接过、机身对需
      设备（无设备 toast 不落库）、未知 id 拒绝；toast「将以 X 常驻」；
      set_bar 两键全清整条退场（不存空壳节）
- [x] 删除 QML 菜单没有的项：「按比例打开」临时启动分支、竖横屏
      菜单项（pyduo togglePortrait 同为无调用点死代码，连带删除）
- [x] 镜像卡右键（QML mirrorContextMenu 逐行）：打开投屏 / hairline /
      窗口栏一级平铺（上巴 沉浸·系统；下巴 沉浸·系统·不显示）→
      setDefaultBarMode；「镜像时关闭设备屏幕」不在 QML 菜单，已删
- [x] 搜索行为：拼音首字母前缀 + 标签小写包含、Ctrl+F 聚焦、
      Esc 先清空再失焦、清空钮 28×28（hover danger 洗色）
- [x] 音量条：未知中性态、拖动视觉先行 + 200ms 防抖落命令
- [x] 会话动作：芯片点击 startAppOnDisplay、✕ stopSession（hover
      danger）、镜像会话禁点
- [x] 键盘：Ctrl+, 设置、Esc 设置页取消返回（settings.reject =
      放弃 draft 重载磁盘，QML cancelled 语义）
- [x] 单测：resolve_fixed_aspect 四分支 / bar_entry_after 全清退场
      + 单边保留（决策提纯为模块级纯函数）
- [x] 菜单浮层像素皮肤 ✅（2026-09-17 完成）：menuFill 实底 + hairline +
      menu_row_style 32px 行高 + 自定义圆点/勾选 + menu_caption 小节头 +
      menu_sub_button 二级菜单；DUO_SHOT_MENU=tile-sub 自动出图链路就绪

### P4 设置页像素移植（✅ 2026-09-17 完成，像素带对拍通过）

- [x] settings.rs 全新像素渲染层（SettingsPage.qml 逐组照抄）：
      引擎卡（scrcpy/adb PathRow：标题行+检测胶囊+输入框+浏览/检测；
      会话运行锁提示；FPS/码率 NumberCell 两格）→ 投屏质量卡（编码
      ModeButton×4 / 音频×3 / 镜像关屏 GlassSwitch + 说明 / DPI 跟随
      开关 + NumberCell（禁用 45% 淡化）+ 说明 / 渲染倍率标签行 +
      滑杆）→ 窗口栏（默认）卡（上巴×2/下巴×3）→ 外观卡（主题×3/
      玻璃开关）；底部保存 PrimaryButton（w76 h32 r10 accent）
- [x] 控件皮肤全自绘：ModeButton（选中 accent14% 底+45% 边+DemiBold）、
      GlassSwitch（40×24 r12 轨+白圆）、NumberBox（−/+ 28px 步进 +
      居中可键入、focus accent 描边）、Slider（4px 轨+16px 白 thumb
      accent 描边）、SecButton、PrimaryButton——零 egui 默认皮肤
- [x] 布局常量逐值：卡缘 16+8（shadowHost 内缩）、卡高 3+24+内容+10
      （阴影宿主上下边）、内容起点 pad+3、caption 21/title 19（Qt
      字体行高校准）、卡间 12、滚区 x16 top64、视口底 footer+8+2
- [x] 保存语义：保存→save_settings→空问题=回主页+resolveAdb 重找
      adb（QML accepted 同构）；Esc/胶囊返回=reject 放弃草稿重载
- [x] 引擎检测：Background 异步 --version（空路径=PATH 扫描），结果
      胶囊 ✓/✗ 文案对齐 pyduo，2.5s 淡出；浏览=rfd native 对话框
- [x] 对拍 qml-settings(-dark).png：文本带序列对齐（9 带中心差
      ≤4.4 DIP=字体 metrics 级）、尾区空带一致、保存按钮像素级
      相同（95px 宽扫描）、GLM light 结构验收通过（dark 尾区带扫描
      佐证；GLM 暗色 DPI 残迹报告经三重像素扫描证伪）
- [x] 单测：SettingsPageModel 草稿↔settings 写入路径 5 项（load/
      save roundtrip、非法枚举拒绝、数值钳制、扩展字段、坏文件红条）
- [x] 设置页开关与说明文字错位微调（✅ 2026-09-17 完成）：
      - 现场证据：`字体错位.png` / `开关错位.png`；
      - 根因：`tso_caption` 累加了双重间距（`ROW_H + SP` 后再 `+ 9.0`），导致说明文字与标题脱节，开关视觉悬空偏高；
      - 对策：收紧息屏开关与说明文字的垂直间距至 20px（行内 gap 5.5px），与下一条目拉开 36px 区分间距（gap 21.5px），消除脱节悬浮感，添加 `settings_layout_y_chain_and_grouping` 单测守护；
      - 验证：消除脱节悬浮感，光暗两主题像素级对齐。
- [ ] 遗留（P5）：控件软阴影（QML MultiEffect blur24——egui painter
      无 blur，视觉差异仅卡外围）

### P5 Windows 真机验收 + 收尾（未开始；持续）

- [x] 交叉构建部署双 exe（mingw；Duo.exe GUI 子系统 + 图标）——
      已于 2026-09-16 完成（本计划前完成，递补记录）
- [ ] accept_windows.ps1 扩展：面板启动/单实例/会话 spawn/退出拖树
- [ ] 真机跑通清单回填（见根 TODO.md「待 Windows 实测」）
- [ ] libmpv 自接视频流（撞到 scrcpy 呈现天花板才启动，暂挂）

## 对拍基线维护

- 基线生成：`.venv/bin/python scripts/qml_shots.py` →
  `docs/validation/assets/qml-{main,settings}{,-dark}.png`（525×825）
- egui 截图：WSLg X11 `import window:duo-panel`（或 grim），存
  `docs/validation/assets/egui-*.png` 同尺寸对照
- 评审：GLM-5.3-flash 视觉 subagent（zai MCP），逐项报 差异→修复
  →复审，直至「结构/间距/色值无明显差异」

## 决策记录（滚动追加）

- 2026-09-16：令牌回滚事件——曾为"提升对比度"自作主张改暗色卡
  alpha（0.10→0.16），被用户纠正：**像素移植不发明数值**，已回滚
  并写入总则 1。
- 2026-09-16：uiScale（DPR<1.25 屏最高 125% 舒适缩放）暂不移植
  ——egui/winit 自带 per-monitor DPI；若真机观感偏小再引入。
- 2026-09-17：菜单毛玻璃（MenuGlassPlate 三明治）不移植——面板
  用 egui 原生 popup + menuFill 不透明回退（Style.qml 的软件回退
  路径本就是不透明实底，视觉合同一致）。
- 2026-09-17：宿主窗口架构收敛回 C# overlay——实验性 Rust win32 SetParent
  宿主（host.rs）退役，统一复用真机验证充分的 C# overlay（chrome.rs +
  chrome_overlay.cs）；duo-core 移去 --embed 旗标，保留 --chrome；所有 Windows
  子进程统一走 quiet_command（CREATE_NO_WINDOW），杜绝控制台黑窗弹跳；
  增加 flex 会话 orientation_lock（wm set-ignore-orientation-request 1）。
- 2026-09-17：菜单皮肤全量落地——skin_menus 注入 menuFill 不透明底色 +
  hairline 分隔线 + 32px 行高 + 4px 圆点，实现右键菜单与 QML 逐像素对齐。
