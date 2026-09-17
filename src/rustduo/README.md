# Duo Rust 栈

Duo 的 Rust 原生实现：`duo-core`（纯逻辑 + 进程层 CLI）与 `duo-panel`
（egui 桌面面板，替代原 PyQt6-QML 实现）。架构演进见 [`plan.md`](../../plan.md) 与
[`plan/todo.md`](../../plan/todo.md)；历史里程碑存档见仓库根 [`TODO.md`](../../TODO.md)。

- `crates/duo-core/src/`：从 `src/pyduo/core/*.py` 逐模块对译，pytest 合同
  逐条镜像为 `#[cfg(test)]`；`mirror` 子命令实现进程内会话编排，
  调用 `chrome.rs`（现场编译 C# overlay `chrome_overlay.cs`）提供无边框交互，
  并以 `quiet_command`（`CREATE_NO_WINDOW`）保证全局无控制台闪屏。
- `crates/duo-panel/src/`：controller.py/Main.qml 的 egui 逐像素直译——
  模型（model.rs）/会话注册表（sessions.rs）/gui_prefs（prefs.rs）/
  拼音索引（pinyin.rs）/后端客户端（backend.rs）/渲染层（app.rs/home.rs/settings.rs），
  业务逻辑与 UI 解耦，app.rs 负责顶层绘制调度与菜单皮肤（skin_menus）。
- `scripts/parity_check.sh`：对译硬保证——同输入下 Python 与 Rust
  输出必须逐字节一致（234 行逐字节一致，含全部 27 张预设 SVG）。

```sh
cargo test                      # 260 passed（duo-core 219 + duo-panel 41）
./scripts/parity_check.sh       # Python/Rust 逐字节对拍（234/234）
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

真机跑 `powershell -File src\rustduo\scripts\accept_windows.ps1`：依次验 duo-core.exe 落位、`devices` 识别、手动 chrome 会话、无孤儿 duo-core/scrcpy 进程，末行看 `结果: PASS`。
