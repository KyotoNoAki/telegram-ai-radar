# 测试用样例数据

全部是虚构的：网站、工具、活动都不存在，网址都用 `example.org` / `example.com`。只给 `tests/` 里的测试用。

| 文件 | 模拟什么 |
|---|---|
| `sample_feed.xml` | 一个 RSS 源：两条相关、两条无关 |
| `sample_feed2.xml` | 一个 Atom 源：与上面重复的一条（网址只差 www 和追踪参数）、一条新的 |
| `news_feed.xml` | 一个新闻 RSS：一条相关、两条用来测试排除词 |
| `broken_feed.html` | 坏掉的源（不是 RSS），用来测试抓取失败时不中断 |
