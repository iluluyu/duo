# 右键菜单毛玻璃：算法与配方（2026-09-09 定稿）

> 本文是 `duo/ui/qml/Main.qml` MenuGlassPlate 与 `Style.qml` 毛玻璃令牌
> 的唯一论述文档。代码里不重复这些内容。

## 1. 结构（三明治）

```
MenuGlassPlate（菜单大小）
├─ ① ShaderEffectSource 快照（过采样，1:1）
│     x/y = 快照原点 − 菜单原点（≈ −28）；尺寸 = sourceRect = 菜单 + 2×28
│     sourceItem = canvasRoot（bgLayer 画布 + 色斑 + StackView 页面）
│     live:false；open() 设 sourceRect + scheduleUpdate() 抓一帧
├─ ② MultiEffect（与 ① 同尺寸）
│     blur 0.75 / blurMax 32 / saturation 0.15
│     maskThresholdMin 0.5 + maskSpreadAtMin 0.4
│     maskSource = 过采样坐标系里与菜单同位的圆角白块（透明底）
└─ ③ 染色矩形（菜单大小，radius 12）
      GL：menuTint 72% 画布色 #B8F5F5F7（二级浮层 menuTintHi 82% 白）
      描边：menuBorder 8% 黑（二级 menuBorderHi 14% 黑）
      软件回退：不透明 menuFill #F7F7F9 + menuFillBorder 10% 黑
```

## 2. 硬规则

1. **过采样 1:1**：快照层尺寸 == sourceRect 尺寸 == 模糊层 == 蒙版底板。
   玻璃内容与真实背景像素对位；贴窗边时快照原点内收、显示偏移同步
   补偿（`menuOffX = sx − px`），1:1 恒成立。
2. **28px 边距 > 模糊核触及半径**（0.75×32 = 24px）：菜单可见边缘的
   模糊核心永远采到真实内容，不碰纹理边界。
3. **maskThresholdMin 0.5 是裁切开关**：MultiEffect 默认阈值窗 0..1 =
   恒等映射，蒙版从不裁切（蒙版缺它 = 过采样模糊层整块方形透出，
   即"两层玻璃"）。
4. **maskSpreadAtMin 0.4 是亚像素坡**：smoothstep 阈值窗 [0.1, 0.9]；
   spread 是归一化 alpha 窗，硬 step 在 125%/150% DPR 下相位抖动出毛刺。
5. **autoPaddingEnabled 必须关**（真机 1.25× DPR 实测，2026-09-09）：
   默认 autoPadding 扩大 MultiEffect 绘制边界，蒙版纹理被拉伸到扩大后
   的边界 → 裁切落在比菜单大一圈、带偏移的圆角矩形上，菜单外溢出
   ~14k 像素 = "两层玻璃 + 外层圆角毛刺"；关掉后溢出归零。28px 过采样
   边距使 autoPadding 毫无必要（可见边缘的模糊核心采不到纹理边界）。
5. **坐标 Math.round**：菜单 x/y 取整，两层光栅化（纹理 vs 矢量）的
   半像素相位差断根。
6. **设备像素网格吸附**：分数 DPR（1.25/1.5）下，菜单位置/快照几何只有
   落在设备像素整数上（snapGrid：1.25→4px 格、1.5→2px 格）才能让快照与
   显示两级采样完全对齐；偏网格 1 逻辑像素会重采样出 ~0.5-1 设备像素的
   玻璃内容漂移（"有时候偏移"）。菜单打开期间快照 live 连续采样（首帧
   永不吃旧纹理），关闭即停。
7. **蒙版只做 alpha 裁切**（直角 bug 根治结构）：不要改回方形采样层。
7. **软件回退路径**（!Style.glassBlur，WSL/软件后端）：①②隐藏（不跑
   着色器即免采样），③换不透明 menuFill。

## 3. 配方依据（两轮外审）

- **Opus 审计（方案 A「Win11 Acrylic」）**：快照源必须含画布（原 stack
  只含半透明卡片，模糊对透明底凭空合成白雾 = "白光"元凶）；tint 用
  **画布同色**而非纯白（纯白在 #F5F5F7 上 +ΔL* 亮度跳变 = "发光"）；
  saturation 补偿染色漂白；色斑需可感知（ΔE>1）玻璃才有折射内容。
- **gemini-3.8-flash 终审**：blur 提到等效 24px（15.6px 打不碎背景文字，
  透出"脏灰斑"）；**描边 8% 黑 hairline**（25% 白在浅画布 ΔE<1 =
  无边框，菜单溶进背景）；spread 0.4 亚像素坡；二级浮层 elevation
  阶梯（tint 更实 + 边更重，零阴影铁律下的分层替代）。
- 色斑：同心三层 Rectangle 逼近径向衰减，核心 alpha 0x14–0x16
  （蓝左上 / 绿右下），在 72% 白卡下画布只微微变彩，玻璃有彩可折。

## 4. 迭代史（真机反馈 → 根因）

| 轮 | 反馈 | 根因 | 修复 |
|---|---|---|---|
| 1 | 白光、死白、质感差 | 快照源 = stack（透明底） | 快照源换 canvasRoot + 配方 A |
| 2 | 背景会移动位置 | sourceRect 外扩 16px 塞进菜单大小显示项（缩放 ≈0.8 且贴边后漂移） | 过采样 1:1 |
| 3 | 两层玻璃 + 外层圆角毛刺 | maskThresholdMin 默认 0 = 蒙版从不裁切，过采样层整块透出 | threshold 0.5 + spread 0.4 + 黑 hairline |
| 4 | 仍有两层（threshold 后） | autoPadding 默认拉伸蒙版到扩大边界 → 裁切比菜单大一圈（真机 1.25× 实测溢出 14k px） | autoPaddingEnabled: false（实测溢出归零） |
| 5 | 偶发内容偏移 | 分数 DPR 下菜单位置偏设备像素网格 → 两级采样分数偏移 | snapGrid 网格吸附 + 打开期间 live 采样 |

## 5. 验收标准（真机）

1. 125%/150% DPI 凑近看四角：连续平滑，无台阶、跳点、毛刺。
2. 菜单盖住背景文字：字形彻底粉碎成灰雾，无笔画残影。
3. 纯空白画布呼出：仅凭 8% 黑细边轮廓 0.1s 可辨，不溶不粘。
4. 蓝绿色斑交界处：温润淡彩琉璃感，非死白非脏灰。
5. 二级浮层：更实更亮的新玻璃板，两级边界清晰，不透视穿透。
6. 玻璃内容与菜单外真实背景严丝合缝（色斑边缘连续），不同落点不漂移。
