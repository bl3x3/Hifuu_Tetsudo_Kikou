# Hifuu_Tetsudo_Kikou
![title](public/assets/title.jpg)


---

## 快速开始

需要 **Python 3.10 或更高版本**，以及电脑、三部手机和可互相访问的局域网。在项目根目录执行：

```sh
python -m pip install --target vendor -r requirements.txt
python run.py
```

Windows 也可以在安装依赖后双击 `启动游戏.bat`。二维码依赖缺失时，仍可手动输入网址和房间码加入。

1. 电脑打开 [主持端](http://localhost:8000/host)，创建房间。主持页面保持前台运行；需要投屏时可另开“观众屏”。
2. 手机连接同一局域网，扫描主持端提供的二维码，输入昵称并准备。手机访问的是电脑的局域网地址，不能使用手机自己的 `localhost`。
3. 三人准备后，主持人分配身份。玩家在手机上确认首程路线，主持人点击“正式发车”。首程选路不限时。
4. 到达所选终点后，游戏暂停供玩家讨论与选路；全部确认后 3 秒自动继续，主持人也可以暂停倒计时或安排未确认者留站。
5. 游戏结束后可查看结局、回放、导出记录，并选择同组重开或创建新房间。

端口占用时运行 `python run.py --port 8001`；仅供本机开发时使用 `python run.py --bind 127.0.0.1`。按 Ctrl+C 停止服务。手机连不上时，检查 Wi-Fi 客户端隔离及电脑防火墙，并手动尝试终端打印的加入地址。

## 游戏内容

- 100 个站点、132 条连接、35 条游戏线路，以及 12 组分散起点。铁路行程为游戏估算，不是真实时刻表。
- 莲子公开身份，梅莉与紫隐藏身份。梅莉可能被秘密侵蚀，改变自己的胜利目标。
- 随机抽线、自由选择沿线终点、改签、途中下车与一次请求留站。
- 东京—京都幻想特急，以及京都、长野、奈良的站点事件。
- 可选“旅途回声”：默认关闭，开启后在第二日零时起按地点与角色触发固定剧情短句。
- 昼夜窗景、角色头像、结局插画、行程回放和独立对局日志。

默认从首日 16:00 开始，每 2 秒推进 15 分钟游戏时间。累计**超过 48 小时**尚未会合会触发超时结局；选路和技术暂停不累计游戏时间。断线 5 秒后暂停，连续中断 90 秒未恢复则中止本局。

完整玩法见服务启动后的 [玩法说明](http://localhost:8000/rules.html)。

## 配置与内容修改

| 文件 | 用途 |
| --- | --- |
| `config/dialogues.json` | 旅途回声对白、角色、触发地点与来源 |
| `config/messages.json` | 站点事件的固定私密提示与对白 |
| `config/rules.json` | 时间推进速度、回声触发规则和站点事件概率 |
| `config/starts.json` | 起点组合与最短路距离约束 |
| `public/game-text.json` | 角色名称、目标说明和结局标题 |
| `data/` | 站点、区间、线路、地图坐标及 SVG |
| `public/assets/manifest.json` | 网页图片与角色、场景、结局的对应关系 |
| `server-config.json` | 可选的本机公网入口配置，已被 Git 忽略 |

字段单位、示例和修改约束见 [配置说明](docs/configuration.md)。服务端配置修改后需重启并新开一局；前端文字和美术修改后刷新页面。默认参数保持原有游戏规则。

### 可选公网入口

默认使用局域网地址。如已自行部署 HTTPS 反向代理，将 `server-config.example.json` 复制为 `server-config.json`，填写自己的入口：

```json
{
  "publicUrl": "https://game.example.com"
}
```

这里是示例域名。配置仅设置允许访问的 Host 和默认加入地址，不会自动创建公网隧道、证书或反向代理。代理上游指向本机 HTTP 服务，并保留外部 Host（含端口）和 Origin；代理应限制公网对 `/host` 和 `/api/rooms` 的访问。主持人在电脑本机创建房间。

临时覆盖可使用 `python run.py --public-url https://game.example.com`；指定其他配置文件使用 `--config 路径`。不创建本机配置文件，或将 `publicUrl` 设为 `null`，即可使用局域网地址。

## 开发与验证

```sh
python -m unittest discover -s tests -v
```

规则、配置和 HTTP 测试使用 Python 标准库，覆盖身份隔离、事件概率、线路与起点、选路暂停、超时边界、存档恢复和权限。

浏览器回归需要额外安装 Playwright 与 Chromium：

```sh
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
python tests/browser_dialogue.py
```

浏览器测试使用临时存档目录，截图与报告写入被忽略的 `test-results/`。真实手机兼容性、现场网络与平衡性仍需实际试玩。

代码入口和维护约定见 [开发说明](docs/development.md)。美术替换见 [素材说明](public/assets/README.md)。

## 存档与发布

`runtime/` 保存本机房间快照和 `runtime/logs/` 对局日志。快照包含玩家凭证及隐藏身份，导出日志也包含玩家昵称和对局记录，应保留在本机。进程异常退出后，重启会恢复房间，并中止尚未结束的对局；已结束的回放可以恢复。

运行必需文件均包含在仓库内，不依赖本机 `design/`、原始 PSD 或历史试玩记录。生成不含 Git 历史、本机配置和存档的源码包：

```sh
python tools/export_source.py
```

输出为 `dist/hifuu-source.zip`。发布范围由 `tools/release-files.txt` 明确列出。**删除当前文件不会删除旧 Git 提交中的内容**；已有历史的仓库在公开前请阅读 [发布说明](docs/publication.md)。

## 许可与素材

项目附带 [GNU GPL v3 许可证](LICENSE)。东方 Project 的角色、世界观与引用文本属于各自权利人；本项目为非官方二次创作。对白的来源和本游戏的地点映射记录在 `config/dialogues.json`。插画及其他第三方内容的授权范围不能仅由代码许可证推定，素材说明见 `public/assets/README.md`。
