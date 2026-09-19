# Duo TODO

> 面向接手实现的 AI。坚持 KISS：一次只做一个编号任务；没有 Windows 实测
> 证据不宣称完成；不加入用户未要求的功能。历史路线（0.x 里程碑、
> Python→Rust 迁移计划）见 [docs/history/](docs/history/)。

## 待 Windows 真机验收（优先）

- [ ] **Rust 栈全面验收**：deploy 双 exe 后真机跑——面板渲染（设备卡/
      固定卡/搜索/网格/右键全菜单/运行卡/Toast/设置页）、会话 spawn
      （mirror argv 合同）、图标 sweep、音频仲裁、单实例提示、退出拖树。
      `src\rustduo\scripts\accept_windows.ps1` 回填 Duo.exe 路径后复用。
- [ ] **面板新交互回归**：顶栏胶囊/固定卡/搜索/右键菜单（含按比例打开
      16:9 与 9:16 各一例）/运行卡 hover 关闭。
- [ ] **玻璃 §8 管线验收**：①上巴胶囊/下巴 §8.6 增益链（BakeGlassPlate，
      暗/亮单位档随底自适应）——暗视频上墨字可读、亮视频上胶囊不溶底
      不冲白；②右键菜单亮色档（地板 0.66/天花 0.975）；③`--glass 0`
      回归（普通材质未受影响）；④DPI 125%/150% 采样边距与模糊核不触边
      （margin=3σ=24 device px）。
- [ ] **第二批五修复验收**：①菜单晃动/二级玻璃时有时无（玻璃矩形量化 +
      泵清空宽容化）；②镜像贴比闪烁（FitWhenArmed 提早到 +200ms 且绕过
      500ms 节流）；③设置页改返回首页自动保存；④应用退出后运行卡芯片
      滞留（update 泵补 sessions.reap + 500ms 重绘）；⑤酷安专属：菜单
      大 + 子菜单回退无玻璃（疑似磁贴靠边/弹层翻转）。
- [ ] **四修复验收**：①右键菜单毛玻璃与 hairline 描边贴合（四角无楔形
      空隙）；②菜单收紧后观感（行间距 0，高度回到 QML 同款）；③比例
      二级菜单右侧示意矩形 + 21:9 机身竖屏启动无两侧大黑边；④镜像/
      固定窗初开即铺满无 letterbox（已部分验证）。
- [ ] **打包版行为回填**：按 docs/windows-setup.md §3 安装到
      `%LOCALAPPDATA%\Duo` 后复验快捷方式/卸载/数据目录。
- [ ] **机身比例预设**：wm size 派生的横/竖预设真机验证。
- [ ] **混用回归**：固定比例会话（--width/--height fixed）与 flex 自由
      窗口的混用。

## 排查存档（复现时先读）

- **21:9 酷安首两叉会话夭折**（2026-09-18）：复现实测窗口客户区恰为
  3360×1440、视频无 letterbox、贴比收敛正常；用户所见「大黑边」= 前两次
  scrcpy 启动在 Renderer 前夭折遗留的黑色无视频窗口。原因未复现（无错误
  行，疑 D3D11/设备端竞态）；再现时保留会话日志 + tasklist 快照。

## 挂起（条件启动）

- [ ] **libmpv 自接视频流**：撞到 scrcpy 呈现天花板再启动。
- [ ] **控件软阴影**：QML MultiEffect blur24 的 egui 等价物（painter 无
      blur，视觉差异仅卡外围）。
- [ ] **中文输入**：uhid 候选窗落物理屏是否复现 → 决定
      `--display-ime-policy=local`。
- [ ] **piliplus 全流程复验**：首页→视频→全屏→拖窗缩放不中断播放
      （拖窗链路已 2026-09-06 真机验证）。
- [ ] **空 flex 会话**：无 `--app` 且 decorations 开启下的无帧降级体验。

## 设计边界（不可越）

- **镜像 / 固定虚拟屏**：窗口贴合视频比例，只做等比缩放；不拉伸/裁切
  消黑边。
- **flex**：恒定 2560×1440/480 虚拟屏；窗口自由拖改；方向信 APP，
  零干预。
- **圆角**：默认系统圆角；G2 连续曲率为长期选开实验（`corner_mode="g2"`）。
- **C# overlay**：保持 C# 5 兼容（legacy `csc.exe` 现场编译）。

## 出图 / 对拍回路（开发常备）

- 面板截图：`duo-panel --shot <out.png> --page home|settings
  --theme light|dark`；菜单出图 `DUO_SHOT_MENU=tile-sub
  DUO_SKIP_SWEEP=1 DUO_SHOT_SEED_APPS=8`。
- Windows 侧桩：`%USERPROFILE%\.local\share\duo\tools\
  duo-core-stub.{bat,py}`（watch=单设备在线、apps=目录全装中文标签、
  其余静默 rc0）；env 透传用 `WSLENV='DUO_CORE_BIN:DUO_DATA_DIR'`
  （冒号分隔）。
- 视觉冻结参照：`docs/validation/assets/qml-*.png`（pyduo 基线，
  不可再生成，作历史合同）。
