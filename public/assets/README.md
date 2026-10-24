# 本地美术资源

本目录包含游戏实际加载的 WebP，下载后即可使用。可选的原始图片与 PSD 不随源码提供；自行准备原图时可放入根目录 `assets/`（已被 Git 忽略）。全部本地加载，支持局域网离线运行。更新已导出的源图后，运行 `python tools/prepare_art.py`（需要 Pillow），转换保持原尺寸、比例与完整画布，不裁切、不重绘。普通 WebP 质量为 88，帽子使用无损 WebP 保留透明通道；游戏运行不需要 Pillow。

导出工具优先读取根目录 `assets/`，该目录不存在时回退到 `design/assets/`；也可通过 `--source-dir` 指定原图目录。位置参数可选，只导出列出的原始文件名；省略时导出全部已登记素材。例如，仅更新本次四张素材：

```sh
python tools/prepare_art.py --source-dir design/assets 堇子.jpg 莲子.jpg 帽子.png 堇子cg.jpg
```

`manifest.json` 管理图片对应关系：

| 源文件 | 插槽 | 游戏用途 |
| --- | --- | --- |
| 标题页.png | scenes.title | 首页、主持入口、候车大厅 |
| avatar0.jpg | roles.unknown | 候车时的未知身份头像 |
| avatar_renko.jpg | roles.renko | 莲子私人身份页、结局揭晓 |
| avatar_hearn.jpg | roles.maribel | 梅莉私人身份页、结局揭晓 |
| avatar_yukari.jpg | roles.yukari | 紫私人身份页、结局揭晓 |
| 堇子.jpg | roles.sumireko | 堇子私人身份页、结局揭晓；导出为 avatar-sumireko.webp |
| 莲子.jpg | appearance.brown | 莲子与堇子未公开身份时共用的问号头像；导出为 avatar-brown.webp |
| 帽子.png | appearance.hat | 直接覆盖在公开棕色头像上的透明帽子；导出为 hat.webp |
| avatar0.jpg | appearance.purple | 梅莉与紫共用的公开问号头像；沿用 avatar-unknown.webp |
| cg01.png | endings.hifuu | 相逢结局 |
| cg02.jpg | endings.yukari_direct / yukari_corrupted | 紫获胜，包括侵蚀梅莉后获胜 |
| cg03.jpg | endings.draw | 超时／未相逢结局 |
| 旅程中止背景.png | endings.aborted | 主持人中止、断线中止 |
| 堇子cg.jpg | endings.sumireko_loss | 最终结算时堇子集体失败的画面；导出为 ending-sumireko-loss.webp |

`roles.sumireko / appearance.brown / appearance.hat / endings.sumireko_loss` 分别对应 `/assets/avatar-sumireko.webp`、`/assets/avatar-brown.webp`、`/assets/hat.webp`、`/assets/ending-sumireko-loss.webp`。

堇子失败图只在最终结算且堇子集体失败时展示，中止不计为集体失败。堇子的手机以该图作为主结局图，主持端与其他玩家手机将其作为原结局图之外的附图。途中堇子被紫抓到时不展示，堇子集体胜利时也不展示。

累计游戏时间超过48小时仍未结束时，自动触发 `draw`，通过现有结局插槽显示 CG03。首程选路不限时，由主持人正式发车；选路及相遇结果全部确认后 3 秒自动继续，主持人可以暂停倒计时。主持人只能安排未选路者留站，不能代替相遇参与者确认私密结果。暂停期间不累计游戏时间。

候车大厅与首页共同使用 `scenes.title`；`scenes.lobby` 保留为旧大厅素材。`scenes.entry / day / night / paper` 分别控制手机入口、大屏日间窗景、夜间窗景、手帖底纹；手机乘车页改为显示本线线路图，不再使用窗景。昼夜由公共游戏时钟决定。旧版标题车厢母图仍由导出工具保留为 `title-carriage.webp`。

首页与候车大厅在窄屏上完整展示标题插画，内容放在图片下方。结局 CG 保持完整 16:9 构图，手机文案排在图外。结束后揭晓全部角色。图片加载失败时保留文字与占位画面。

## 头像替换入口

`manifest.json` 的 `roles.renko / maribel / yukari / sumireko` 管理私人头像。现有莲子、梅莉和紫的美术保持原样；堇子使用 `avatar-sumireko.webp`。已有的 `avatar-sumireko.svg` 留存但不加载。

收到素材后，将图片放入本目录，并把 `roles.renko / maribel / yukari / sumireko` 对应值改为例如 `/assets/新文件名.webp`。私人身份页与赛后揭晓会读取这些路径。建议使用透明背景方形图片；显示时保持原比例，不裁切角色。

公开头像另由 `appearance.brown / purple` 管理，地图、公开记录和台词卡均从这两个插槽读取。棕色组使用 `avatar-brown.webp`，紫色组沿用 `avatar0.jpg` 已导出的 `avatar-unknown.webp`。棕色组内和紫色组内必须分别共用同一张图，不能通过角色、姓名或座位选用不同人物头像。

`appearance.hat` 使用 `hat.webp`。帽子与共用棕色头像的原图均为 2048×2048，导出保留完整画布，按相同位置、尺寸和比例直接叠加；不要单独裁掉帽子的透明留白或另行居中缩放。所有私人、公开与地图头像均使用圆形裁切。显示时棕色头像与帽子共同放大 1.6 倍，并一起裁入同一个圆形区域，裁去画布四周留白。公开棕色头像本身不带帽子，只有戴帽状态才叠加帽子。堇子私人头像保持原比例，头像白底沿用原图。

缺失路径、`null` 或图片加载失败都会退回中性占位，不影响行程和确认操作。更换素材及清单后刷新页面即可；无须修改游戏代码。正式发布时，将新增素材文件加入 `tools/release-files.txt`。

## 素材来源与再使用

东方 Project 角色、世界观和引用文本属于各自权利人。本目录未附逐张插画的作者及授权证明；再分发素材或替换素材时，应补充相应署名与授权信息。项目代码的 GPL 许可不代替第三方素材的授权。
