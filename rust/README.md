# duo-core（Rust 第一档，TODO 0.2）

Duo 核心逻辑的 Rust 下沉。路线与进度见仓库根 `TODO.md` §0；
嵌入实验设计见 `docs/window-experience.md` §14。

- `crates/duo-core/src/`：从 `duo/core/*.py` 逐模块对译，pytest 合同
  逐条镜像为 `#[cfg(test)]`；零外部依赖。
- `scripts/parity_check.sh`：对译硬保证——同输入下 Python 与 Rust
  输出必须逐字节一致（当前 315/315 行，含全部 27 张预设 SVG）。

```sh
cargo test                      # 57 合同测试
./scripts/parity_check.sh       # Python/Rust 逐字节对拍
cargo clippy --all-targets && cargo fmt --check
```

## WSL 内直接产出 Windows exe（2026-09-16 打通）

前置（一次性）：`sudo pacman -S --needed mingw-w64-gcc` +
`rustup target add x86_64-pc-windows-gnu`。

```sh
PATH="/usr/x86_64-w64-mingw32/bin:$PATH" \
  cargo build --target x86_64-pc-windows-gnu --release
cp target/x86_64-pc-windows-gnu/release/duo-core.exe \
  /mnt/c/Users/Administrator/.local/share/duo/tools/   # duocore.py ③号查找位
```

## Windows 验收

真机跑 `powershell -File rust\scripts\accept_windows.ps1`：依次验 duo-core.exe 落位、`devices` 识别、手动 embed 会话、无孤儿 duo-core/scrcpy 进程，末行看 `结果: PASS`。
