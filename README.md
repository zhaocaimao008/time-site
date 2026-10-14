# 世界时间

全自动静态网站:世界时钟、49 个城市当地时间与北京时差、热门城市两两时差、夏令时时间表、时区换算器。
时区和夏令时规则来自 IANA 时区数据库(Python `zoneinfo`,浏览器 `Intl`),无需外部数据。
GitHub Actions 每天北京时间 00:15 重新生成,夏令时切换后页面上的时差自动更新。

页脚"更多实用工具"链接到同系列网站,列表在 `config.json` 的 `network` 中维护。

## 本地测试

```bash
python3 build.py --today 2026-10-14 && python3 -m http.server -d dist
```
