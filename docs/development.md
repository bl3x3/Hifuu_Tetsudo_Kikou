# 开发说明

## 从哪里开始

| 模块 | 职责 |
| --- | --- |
| `run.py` | 将可选 vendor 依赖加入搜索路径，启动 HTTP 服务 |
| `server/settings.py` | 读取 JSON 并校验字段、类型、概率和对白引用 |
| `server/data.py` | 读取路网，构造邻接表和线路，处理公开地图 SVG |
| `server/starts.py` | Dijkstra 最短路、起点距离检查与模板生成 |
| `server/dialogue.py` | 筛选对白候选、生成公开地点和已触发对白投影 |
| `server/game.py` | 权威规则、游戏推进、动作处理与公私状态投影 |
| `server/journal.py` | 现实时间记账和去除凭证后的日志结构 |
| `server/web.py` | HTTP 路由、凭证与来源校验、加锁、持久化和恢复 |
| `public/app.js` | 主持、大屏和手机界面、状态轮询、动作提交与回放 |

## 游戏状态与时间

房间流程为 `lobby → briefing → running ↔ decision → ended`。技术暂停保存在独立的 `technical` 字段中。`game.advance()` 根据单调时钟推进，`rail_step()` 结算游戏时间；日志墙钟时间只用于记录，不能反过来驱动游戏规则。

玩家动作带版本与决策批次，陈旧选路抛出 `StaleAction`，客户端应重新获取选项。HTTP 在持有房间锁时处理动作、保存快照；保存失败会恢复动作前状态。对局结束日志写入失败时保留已完成快照并重试，不回退胜负结果。

`public_state()` 是公开字段白名单，`private_state()` 在此基础上补入单个玩家自己的信息。新加私密字段时，应在投影处明确控制可见性，不要把整个房间对象发送到浏览器。静态服务根目录始终为 `public/`。

## 内容与代码

固定对白和数值修改优先使用 `config/`；无需在规则分支中寻找文字。字段和单位见 [配置说明](configuration.md)。`design/` 仅为本机设计资料，不再参与运行时读取。

Python 源码按 `pyproject.toml` 中的 Black 配置格式化；前端保持普通 ES module，无需构建。可选维护命令：

```sh
python -m pip install black==26.5.1
python -m black server run.py tools tests
npx --yes prettier@3.6.2 --write public/app.js public/index.html
```

Node.js 仅用于可选的格式化，不是游戏依赖。修改规则或网络服务后运行 `python -m unittest discover -s tests -v`；修改前端后运行相关 `tests/browser_*.py`。测试使用临时数据目录，不要将测试进程指向真实 `runtime/`。

发布包采用 `tools/release-files.txt` 文件白名单。新增运行依赖文件后，将其加入清单，并解压发布包运行测试，避免本机私有文件掩盖缺失依赖。
