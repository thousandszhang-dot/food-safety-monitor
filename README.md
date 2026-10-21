# FoodWatch 全球食品安全通报

这是一个可独立托管的静态网站。浏览器只需加载 `index.html`，不依赖 Codex、Gemini、服务器端数据库或第三方前端 API。

## Windows 本地打开

将整个文件夹复制到 Windows 电脑，双击 `index.html`，使用 Edge、Chrome 或其他现代浏览器打开。当前数据已内嵌在网页中，因此初次查看不需要安装 Python 或 Codex。

## 发布为公开网址并自动更新

`public/` 目录是部署的网站根目录；源码、采集脚本与快照保存在仓库根目录。工作流支持手动运行，也会在每天 **08:00 与 13:00（Asia/Shanghai）** 采集欧委会 DG SANTE RASFF API、重建 HTML 快照并部署页面。初次启用前，在仓库 **Settings → Secrets and variables → Actions** 添加 `SANTE_SUBSCRIPTION_KEY` 密钥；密钥不会写入仓库。

工作流按 `Asia/Shanghai` 时区在 08:00 和 13:00 触发。RASFF 接口暂时不可用时，该次工作流失败，已有线上页面不会被空数据覆盖；下一个时段会再尝试。

工作流把更新后的压缩 RASFF JSON 与压缩 HTML 源文件提交回 `main` 分支；运行时自动解压、增量更新，再将网页发布到 `public/`。仓库需允许 GitHub Actions 使用 `GITHUB_TOKEN` 写入内容，并且 Pages 需选用 GitHub Actions 部署。

首次发布后，GitHub Pages 会提供一个 `https://<账户>.github.io/<仓库>/` 网址，可在 Windows 或其他设备的浏览器访问。当前目录尚未绑定 GitHub 仓库或公开网址。

## 数据范围和字段限制

页面包含韩国 MFDS 当前采集快照及截至 2026-10-20 的 RASFF 历史快照。RASFF 公共接口未提供公司名称、结构化检出值或逐条适用限量，页面相应字段标记为未提供。常见 RASFF 品名提供中文参考译名；未命中的品名保留源文并标注未译。

官方 RASFF 来源：<https://webgate.ec.europa.eu/rasff-window/screen/search>
