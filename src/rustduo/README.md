# Duo Rust 栈

Duo 的原生实现：`duo-core`（纯逻辑 + 进程层 CLI）与 `duo-panel`
（egui 桌面面板）。初代 Python 栈的对译迁移已完成并退役（生平见
[`docs/history/pyduo.md`](../../docs/history/pyduo.md)，迁移计划存档见
[`docs/history/migration-plan.md`](../../docs/history/migration-plan.md)）。

- `crates/duo-core/src/`：纯逻辑层（engine / aspects / catalog /
  icon_presets / settings / paths / glass）+ 进程层（devices / watch /
  session / mirror / apps / set-volume / audio-lock，JSON-lines 协议）；
  `mirror` 子命令实现进程内会话编排，调用 `chrome.rs`（现场编译 C#
  overlay）提供无边框交互，并以 `quiet_command`（`CREATE_NO_WINDOW`）
  保证全局无控制台闪屏。
- `crates/duo-core/resources/`：编译期嵌入的实源资产——
  `chrome_overlay.cs`（C# overlay 源码）、`duo_icons.dex` +
  `duo_icon_renderer.java`（设备端图标渲染）。
- `crates/duo-panel/src/`：业务逻辑与 UI 解耦——模型（model.rs）/会话
  注册表（sessions.rs）/gui_prefs（prefs.rs）/拼音索引（pinyin.rs）/
  后端客户端（backend.rs）/渲染层（app.rs/home.rs/settings.rs），
  app.rs 负责顶层绘制调度与菜单皮肤（skin_menus）。
- `assets/`：`duo.ico`（exe 资源图标）+ `duo.png`（面板 logo）。

```sh
cargo test                      # 282 passed（duo-core 227 + duo-panel 55）
cargo clippy --all-targets && cargo fmt --check
```

## WSL 内直接产出 Windows exe

前置（一次性）：`sudo pacman -S --needed mingw-w64-gcc` +
`rustup target add x86_64-pc-windows-gnu`。

```sh
./scripts/build_wsl.sh --deploy    # 双 exe 交叉构建并部署到 tools/
# 等价手工：
PATH="/usr/x86_64-w64-mingw32/bin:$PATH" \
  cargo build --target x86_64-pc-windows-gnu --release
cp target/x86_64-pc-windows-gnu/release/duo-core.exe \
  /mnt/c/Users/Administrator/.local/share/duo/tools/
cp target/x86_64-pc-windows-gnu/release/duo-panel.exe \
  /mnt/c/Users/Administrator/.local/share/duo/tools/Duo.exe
```

产物：`duo-core.exe`（console CLI）+ `Duo.exe`（面板，GUI 子系统 +
assets/duo.ico 资源图标 + 单实例互斥体；同目录自动探测并调用 duo-core.exe）。

Windows 侧本机构建备选：`src\rustduo\scripts\build_windows.ps1 -Deploy`。

## 安装（Rust 面板版）

```powershell
powershell -File scripts\install-windows.ps1 C:\duo\src\rustduo\target\x86_64-pc-windows-gnu\release\duo-panel.exe
```

Duo.exe 与 duo-core.exe 同目录装入 `%LOCALAPPDATA%\Duo`，桌面/开始
菜单快捷方式与卸载项由脚本建好。

## Windows 验收

真机跑 `powershell -File src\rustduo\scripts\accept_windows.ps1`：依次验 duo-core.exe 落位、`devices` 识别、手动 chrome 会话、无孤儿 duo-core/scrcpy 进程，末行看 `结果: PASS`。活任务清单见仓库根 [`TODO.md`](../../TODO.md)。

## 出图调试

```sh
duo-panel --shot out.png --page home|settings --theme light|dark
DUO_SHOT_MENU=tile-sub DUO_SKIP_SWEEP=1 DUO_SHOT_SEED_APPS=8 duo-panel --shot menu.png
```

配合 Windows 侧桩 `duo-core-stub.bat`（见根 TODO.md「出图 / 对拍回路」）
可脱离设备出图；历史视觉基线 `docs/validation/assets/qml-*.png` 为
冻结参照（不可再生成）。
