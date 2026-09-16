# duo Rust 栈（TODO 0.2/0.3/0.4）

Duo 的 Rust 下沉：`duo-core`（纯逻辑 + 进程层 CLI）与 `duo-panel`
（egui 面板，替代 PyQt6-QML）。路线与进度见仓库根 `TODO.md` §0；
嵌入实验设计见 `docs/window-experience.md` §14。

- `crates/duo-core/src/`：从 `duo/core/*.py` 逐模块对译，pytest 合同
  逐条镜像为 `#[cfg(test)]`；`mirror` 子命令 = `duo mirror --duo-core`
  的进程内替代（解析/规划/宿主窗全量）。
- `crates/duo-panel/src/`：controller.py/Main.qml 的 egui 对译——
  模型（model.rs）/会话注册表（sessions.rs）/gui_prefs（prefs.rs）/
  拼音索引（pinyin.rs）/后端客户端（backend.rs）/渲染层（app.rs），
  业务逻辑全部在无 UI 依赖的可测模块里，app.rs 只做绘制转发。
- `scripts/parity_check.sh`：对译硬保证——同输入下 Python 与 Rust
  输出必须逐字节一致（当前 315/315 行，含全部 27 张预设 SVG）。

```sh
cargo test                      # duo-core 214 + duo-panel 33
./scripts/parity_check.sh       # Python/Rust 逐字节对拍
cargo clippy --all-targets && cargo fmt --check
```

## WSL 内直接产出 Windows exe（2026-09-16 打通）

前置（一次性）：`sudo pacman -S --needed mingw-w64-gcc` +
`rustup target add x86_64-pc-windows-gnu`。

```sh
./scripts/build_wsl.sh --deploy    # 双 exe + 部署到 tools/
# 等价手工：
PATH="/usr/x86_64-w64-mingw32/bin:$PATH" \
  cargo build --target x86_64-pc-windows-gnu --release
cp target/x86_64-pc-windows-gnu/release/duo-core.exe \
  /mnt/c/Users/Administrator/.local/share/duo/tools/
cp target/x86_64-pc-windows-gnu/release/duo-panel.exe \
  /mnt/c/Users/Administrator/.local/share/duo/tools/Duo.exe
```

产物：`duo-core.exe`（console CLI，duocore.py ③号查找位）+
`Duo.exe`（面板，GUI 子系统 + assets/duo.ico 资源图标 + 单实例互斥体；
同目录自动探测 duo-core.exe）。

Windows 侧本机构建备选：`rust\scripts\build_windows.ps1 -Deploy`。

## 安装（Rust 面板版）

```powershell
powershell -File scripts\install-windows.ps1 C:\duo\rust\target\x86_64-pc-windows-gnu\release\duo-panel.exe
```

Duo.exe 与 duo-core.exe 同目录装入 `%LOCALAPPDATA%\Duo`，桌面/开始
菜单快捷方式与卸载项由脚本建好。

## Windows 验收

真机跑 `powershell -File rust\scripts\accept_windows.ps1`：依次验 duo-core.exe 落位、`devices` 识别、手动 embed 会话、无孤儿 duo-core/scrcpy 进程，末行看 `结果: PASS`。
