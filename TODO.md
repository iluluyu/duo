# Duo TODO

> 面向接手实现的 AI。坚持 KISS：保留 Python / PyQt6-QML + scrcpy + C# overlay，不重写视频链路。
> 一次只做一个编号任务；没有 Windows 实测证据不宣称完成；不加入用户未要求的功能。

## 边界

- **镜像 / 固定虚拟屏**：窗口贴合视频比例，只做等比缩放；不用拉伸/裁切消黑边。
- **应用会话（flex）**：固定 2560×1440/480 虚拟屏；窗口自由拖改；不跟随、不切横竖屏（方向信 APP）。
- **圆角**：默认系统圆角；G2 连续曲率为长期选开实验（`corner_mode="g2"`）。

## 1. ~~P1 — 动态跟随虚拟屏~~（已回退，2026-09-06 用户拍板）

> 实现+真机验证后同日整体回退：不要"显示跟随窗口"、不要横竖屏切换/防转钉扎，
> **信 APP**：虚拟屏恒定 2560×1440，方向由 APP 自主请求（app 全屏→Android 转屏
> →scrcpy 窗口随视频自然重排），我们零干预。保留：自由窗口（拖拽缩放，异步
> SetWindowPos 跟手）。全天 A/B 死路径与旋转风暴实验存档见 docs/window-experience.md §3。
> 曾实现的就地跟随（`wm size -d` settle 下发，真机可用）已删——历史代码见 git
> 9720fe7，勿凭"已验证"复活。

## 待 Windows 实测（收尾清单）

- [ ] 按 docs/windows-setup.md 清单正式回填打包版行为（onefile → `C:\Tools\Duo.exe`）
- [ ] 空 flex 会话（无 `--app`）decorations 开启下的无帧降级体验
- [ ] 中文输入：uhid 候选窗落物理屏是否复现 → 决定 `--display-ime-policy=local`
- [ ] piliplus 全流程（首页→视频→全屏→拖窗缩放不中断播放）：拖窗链路已真机验证
      （2026-09-06，源码+打包 exe），播放中缩放复验待回填

## 当前基线（2026-09-06）

- 应用会话：固定 2560×1440/480 虚拟屏 + `--no-window-aspect-ratio-lock`；窗口自由
  拖改、缩放异步下发（SWP_ASYNCWINDOWPOS）跟手；无钉扎/无显示跟随/无横竖屏干预
  （方向信 APP，2026-09-06 回退决策）。
- 测试 179 passed；ruff / mypy 全绿；overlay 经 csc.exe 真机编译通过。
