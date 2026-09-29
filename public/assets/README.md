# 本地美术资源

原始图片与 PSD 保存在根目录 `assets/`，本目录保存游戏实际加载的 WebP。全部本地加载，支持局域网离线运行。更新已导出的源图后，运行 `python tools/prepare_art.py`（需要 Pillow），转换保持原尺寸与构图，WebP 质量 88；游戏运行不需要 Pillow。

`manifest.json` 管理图片对应关系：

| 源文件 | 插槽 | 游戏用途 |
| --- | --- | --- |
| 标题页.png | scenes.title | 首页、主持入口、候车大厅 |
| avatar0.jpg | roles.unknown | 候车、隐藏身份的公共到站记录与地图标记 |
| avatar_renko.jpg | roles.renko | 莲子私人身份页、结局揭晓 |
| avatar_hearn.jpg | roles.maribel | 梅莉私人身份页、结局揭晓 |
| avatar_yukari.jpg | roles.yukari | 紫私人身份页、结局揭晓 |
| cg01.png | endings.hifuu | 相逢结局 |
| cg02.jpg | endings.yukari_direct / yukari_corrupted | 紫获胜，包括侵蚀梅莉后获胜 |
| cg03.jpg | endings.draw | 超时／未相逢结局 |
| 旅程中止背景.png | endings.aborted | 主持人中止、断线中止 |

累计游戏时间超过48小时仍未结束时，自动触发 `draw`，通过现有结局插槽显示 CG03。只有首程选路保留30秒倒计时并在超时后自动留站；到站讨论由主持人手动继续。选路和技术暂停期间不累计游戏时间。

候车大厅与首页共同使用 `scenes.title`；`scenes.lobby` 保留为旧大厅素材。`scenes.entry / day / night / paper` 分别控制手机入口、日间窗景、夜间窗景、手帖底纹。昼夜由公共游戏时钟决定。旧版标题车厢母图仍由导出工具保留为 `title-carriage.webp`。

首页与候车大厅在窄屏上完整展示标题插画，内容放在图片下方。结局 CG 保持完整 16:9 构图，手机文案排在图外。进行中的公共到站记录和地图显示莲子的公开头像，梅莉与紫继续使用未知身份头像，结束后揭晓全部角色。图片加载失败时保留文字与占位画面。
