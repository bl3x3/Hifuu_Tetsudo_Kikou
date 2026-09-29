# 配置说明

所有 JSON 使用 UTF-8；不支持注释和尾随逗号。服务启动时加载 `config/`，遇到字段缺失、未知字段、非法概率或不存在的站点会报错。修改后先运行单元测试，再重启服务并新开一局。

## 时间与概率：config/rules.json

游戏使用 tick 表示 15 分钟游戏时间；这个单位与站点数据和界面一致，不作为可单独修改的参数。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `tick_ms` | 2000 | 推进一个 tick 所需的现实毫秒数，正整数 |
| `dialogue.start_tick` | 32 | 开始触发回声的 tick，即首日 16:00 后 8 小时 |
| `dialogue.cooldown_ticks` | 8 | 同一玩家两次成功回声的最短间隔，默认 2 游戏小时 |
| `dialogue.max_per_player` | 2 | 每位玩家每局的回声上限 |
| `dialogue.first_probabilities` | `[0.4, 0.7, 1.0]` | 首次成功前，按失败次数选择概率；超过长度后沿用最后一个值 |
| `dialogue.repeat_probability` | 0.4 | 首次成功后的触发概率 |
| `events.kyoto_protection_probability` | 0.5 | 京都庇护事件概率 |
| `events.kyoto_dialogue_probability` | 0.5 | 京都私密对白事件概率 |
| `events.nagano_probability` | 0.2 | 长野停滞事件概率 |

所有概率必须在 0–1 之间。默认规则测试会验证现行玩法；有意改变平衡参数时，应同步调整有关规则预期与 `public/rules.html` 的说明。48 小时时限、三名玩家、事件先后顺序及庇护期限仍由规则引擎定义，不能通过这些字段更改。

## 固定对白：config/dialogues.json

对象的键是稳定的对白 ID。每条记录包含：

```json
{
  "custom_memory": {
    "role": "maribel",
    "group": "kyoto",
    "stations": ["s060"],
    "text": "此处填写对白。",
    "source": "此处填写作品及章节。",
    "url": "https://example.com/source",
    "mapping": "说明原作场景与游戏地点之间的关系。"
  }
}
```

这是字段示例，新增内容时应填入实际出处。`role` 仅支持 `maribel` 和 `yukari`。`stations` 引用 `data/stations.csv` 的 ID。可选 `edge` 限制抵达所经区间，例如特急 `e131`，此时站点必须是该区间端点。

同一角色、同一 `group` 的对白共享一次地点组机会；成功后从匹配记录中抽一句。失败也会消耗该地点组机会。带 `edge` 的组优先于普通地点组。

历史存档保存对白 ID，修改既有 ID 或删除已使用的对白会使旧局无法完整展示。需要兼容历史回放时保留这些 ID；修改文本也会改变旧回放所显示的文字。旧局快照中的 `tickMs` 会保留原值，其他服务端配置以本次启动为准。

`config/messages.json` 存放京都庇护、侵蚀通知、京都对白、长野停滞和奈良改签等私密文本。保留键名，只修改文字。事件效果由引擎实现，改文字不会改变实际效果。

这些文件不会作为静态资源发送给浏览器，HTTP 只返回已触发的对白和当前玩家的私密提示。源码公开后，读者可以查看预设剧情及规则；当前对局的凭证、身份和事件状态仍由服务器控制。

## 起点：config/starts.json

`groups` 的每一项是三个互不相同、属于不同区域的站点 ID。服务器按数组顺序生成 `S01` 等模板编号，并根据全部区间（包括特急）检查最短路。

- `min_distance_ticks` / `max_distance_ticks`：两两最短行车距离的上下限，默认 20 / 40 tick。
- `max_distance_ratio`：同组最大距离与最小距离之比，默认不超过 1.6。

距离不计讨论时间和中停。调整顺序会改变模板编号，测试与主持端选择也需同步检查。

## 地图与公开内容

`data/stations.csv` 定义站点，`data/edges.csv` 定义连接和游戏时长，`data/lines.json` 将连接组成无分叉、无闭环的连续线路。`data/map_layout.json` 和 `data/network.svg` 定义示意图布局。修改网络时需同步保持 ID、地图元素、坐标、起点和事件引用一致。

`public/game-text.json` 管理公开角色名称、完整目标、简短目标与结局标题；网页会将它们作为文字转义，不能放 HTML。`public/assets/manifest.json` 管理图片路径。两者修改后刷新浏览器即可。

部署配置见根目录 `server-config.example.json`；复制后的 `server-config.json` 属于本机文件，不应包含在源码发布包里。
