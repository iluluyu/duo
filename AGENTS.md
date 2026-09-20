# Duo 项目规范

## 注释纪律（2026-09-09 起）

代码文件里**尽可能少写注释**：

- 实现代码只保留**单行、必要**的注释（标识非显然的 why，如一处反直觉
  的 hack）；禁止多段论述式注释块、迭代历史、参数论证。
- 设计决策、算法推导、配方参数、迭代历史与验收标准一律写进
  `docs/` 下对应文档（UI/材质 → `docs/ui/`，窗口/会话语义 →
  `docs/window-experience.md`，编码/帧率/音频 →
  `docs/mirroring-quality.md`，历史材料 → `docs/history/`），
  代码处最多留一行指路注释（例：`// 毛玻璃配方与算法见 docs/ui/glass-recipe.md`）。
- 测试函数的 docstring 同理：一句话说清被测合同即可，长文背景移入
  docs 或删除。
- 已存在于代码中的长注释，遇到即迁移（改到该文件时顺手搬走），不新写。

## 既有约定

- 实现栈为 Rust（`src/rustduo`，workspace：duo-core + duo-panel）；
  `cargo test` / `cargo clippy --all-targets` / `cargo fmt --check`
  全绿是合入门槛。初代 Python 栈已退役（`docs/history/pyduo.md`），
  仓库内不再引入 Python 运行时依赖（docs/ui/probes 探针脚本除外）。
- C# overlay（src/rustduo/crates/duo-core/resources/chrome_overlay.cs）
  保持 C# 5 兼容（legacy csc.exe 编译）。
- UI 验收标准：docs/ui/DESIGN.md；方案调研存 docs/ui/；活任务清单为
  根 TODO.md。
