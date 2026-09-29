# 公开发布说明

## 当前源码范围

仓库保留运行代码、游戏配置、必要路网数据、网页素材、测试和文档。本机公网配置、真实试玩报告、房间快照、运行日志、缓存和原始设计资料由 `.gitignore` 排除；已跟踪的本机文件须同时从 Git 索引移除。

`server-config.example.json` 仅含 `publicUrl: null`。测试和文档中的公网地址使用保留的示例域名。Git 提交仍会记录提交者信息，提交前可检查 `git config user.name` 与 `git config user.email`，按需要使用 GitHub 提供的隐私邮箱。

## 已有 Git 历史

本项目整理前的历史提交包含旧部署地址、试玩分析与本机缓存。新增一个删除文件的提交，或添加 `.gitignore`，均不会删除历史中的原内容。

本次整理不重写已有历史。首次公开建议从经过检查的源码包建立新仓库：

```sh
python tools/export_source.py
```

将 `dist/hifuu-source.zip` 解压到新的空目录，再执行：

```sh
git init
git add .
git commit -m "Initial public release"
```

在这个新目录关联准备公开的空仓库后再推送。不要复制原来的 `.git/`，也不要直接公开带旧历史的仓库。如果要沿用已有远端及提交历史，需要另行清理全部相关分支和标签；修改历史会影响其他克隆，应单独处理。

## 发布包检查

导出工具只打包 `tools/release-files.txt` 中列出的文件，不读取 Git 历史，也不递归收集未审核文件。清单不是凭证扫描器：修改文件内容或清单后仍需检查是否引入了真实地址、凭证和玩家记录。

解压后的源码应能独立运行：

```sh
python -m unittest discover -s tests -v
python -m pip install --target vendor -r requirements.txt
python run.py
```

对外分享日志前另行去除玩家昵称、房间编号和时间记录。游戏不需要任何真实试玩快照才能运行。
