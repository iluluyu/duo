# pyduo 史话（Python 参考栈，已退役）

> pyduo 是 Duo 的第一代实现与行为基准：Python 3.11 + PyQt6-QML 面板 +
> Python core（scrcpy 引擎编排）+ C# overlay。2026-09-19 随 Rust 栈
> （`src/rustduo`）真机好用后从主干移除；代码完整保存在 git 标签
> **`pyduo-final`**（`9f819e0`）中，随时可找回。

## 生平（2026-09-03 ~ 2026-09-19）

- **快速验证期（09-03 ~ 09-16）**：以最低工程成本验证了「安卓设备作为
  Windows 无头应用服务器」的完整产品形态——整机镜像、2560×1440 flex
  虚拟屏、方向信 APP、音频独占仲裁、C# overlay 无边框窗口、玻璃材质
  （§8 配方）。488 条 pytest 固化全部行为合同；PyInstaller onefile
  产物 `C:\Tools\Duo.exe` 真机日用。
- **对译基准期（09-16 ~ 09-18）**：`src/rustduo` 迁移期间，pyduo 是
  像素级/逐字节对拍的参照系——`parity_check.sh` 保证同输入下 Python 与
  Rust 输出逐字节一致（315 行，含全部 27 张预设 SVG）；QML 截图基线
  （`docs/validation/assets/qml-*.png`）是 egui 面板的视觉合同。
- **退役（09-19）**：Rust 栈代码/测试/交叉构建全绿且真机好用，参考栈
  完成使命，整体移入历史。

## 它留下的东西

- **冻结合同**：媒体音量命令（`cmd media_session volume`）、HOME 全局
  拦截、旋转风暴九轮实验等真机定论，已并入
  [docs/window-experience.md](../window-experience.md) 与
  [docs/mirroring-quality.md](../mirroring-quality.md)。
- **视觉合同**：[docs/ui/DESIGN.md](../ui/DESIGN.md) 的令牌与铁律
  直接继承 QML/Style.qml 定稿；qml 截图基线仍留在
  `docs/validation/assets/`（无法再生成的冻结参照）。
- **活资源**：`resources/chrome_overlay.cs`（C# overlay 源码）、
  `duo_icons.dex` + `duo_icon_renderer.java`（设备端图标渲染）已迁入
  `src/rustduo/crates/duo-core/resources/`，仍是唯一实源（Rust 编译期
  嵌入）；根 `assets/duo.{ico,png}` 迁入 `src/rustduo/assets/`。
- **迁移叙事**：Python→Rust 全量迁移计划与决策记录见
  [migration-plan.md](migration-plan.md)。

## 如何找回

```bash
# 浏览最后形态
git log pyduo-final -- src/pyduo

# 整栈检出（需要 pyproject.toml 一并恢复才能 pip install -e ".[gui,dev]"）
git checkout pyduo-final -- src/pyduo tests pyproject.toml duo.spec gui_entry.py
```

依赖：Python 3.11+，`pip install -e ".[gui,dev]"`（PyQt6 / pytest /
ruff / mypy）。本地 `.venv` 未随仓库分发，如需跑史前探针
（`docs/ui/probes/`）可重建。
