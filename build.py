#!/usr/bin/env python3
"""生成世界时间/时差静态网站到 dist/。

时区和夏令时规则来自 IANA 时区数据库(Python zoneinfo),页面里的实时时钟由浏览器按同一数据库计算。
构建时先把每个城市的 UTC 偏移、与北京的时差和夏令时说明写进 HTML,脚本再改写成实时时间。
"当前时差"按生成当天(北京时间)计算,网站每天自动重新生成,夏令时切换后自动更新。

用法:
    python3 build.py                    # 以北京时间今天为准
    python3 build.py --today 2026-10-14 # 指定日期(测试用)
"""
import datetime as dt
import html
import itertools
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

from cities import CITIES

ROOT = Path(__file__).parent
OUT = ROOT / "dist"
BJ = ZoneInfo("Asia/Shanghai")
WEEK = "一二三四五六日"

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--text:#1d2129;--muted:#6b7280;--line:#e5e7eb;--accent:#4338ca;--soft:#eef2ff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e6e8eb;--muted:#9aa1ac;--line:#2a2f3a;--accent:#a5b4fc;--soft:#1e1b4b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header,main,footer{max-width:860px;margin:0 auto;padding:0 16px}
header{padding-top:16px}header .logo{font-weight:700;font-size:18px;color:var(--text)}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:15px;margin-top:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
h1{font-size:24px;line-height:1.4;margin:8px 0;overflow-wrap:anywhere}h2{font-size:19px;margin:0 0 12px}
.big{font-size:34px;font-weight:700;margin:4px 0;font-variant-numeric:tabular-nums}.muted{color:var(--muted);font-size:14px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left}
.links{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:15px}
.clocks{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}.clocks a{display:block;background:var(--soft);border-radius:10px;padding:10px 12px;color:var(--text)}
.clocks b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.clocks small{color:var(--muted)}
.form{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}.form label{display:flex;flex-direction:column;font-size:14px;color:var(--muted)}
.form input,.form select{font-size:17px;padding:8px 10px;margin-top:4px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);width:100%}
.ad{margin:16px 0;min-height:90px}footer{padding:24px 16px;color:var(--muted);font-size:13px}
"""

# 实时时钟:给带 data-tz 的元素每秒刷新当地时间
CLOCK_JS = """<script>(function(){function upd(){[].forEach.call(document.querySelectorAll('[data-tz]'),function(el){
var o={timeZone:el.dataset.tz,hour:'2-digit',minute:'2-digit',hour12:false};if(el.dataset.s)o.second='2-digit';
var t=new Date().toLocaleTimeString('zh-CN',o);if(el.dataset.d){t=new Date().toLocaleDateString('zh-CN',{timeZone:el.dataset.tz,month:'long',day:'numeric',weekday:'short'})}
el.textContent=t})}upd();setInterval(upd,1000)})();</script>"""


def esc(s):
    return html.escape(str(s))


def ld_html(blocks):
    """把结构化数据写成 head 里的 JSON-LD。转义 <,避免打断 script 标签。"""
    parts = []
    for obj in blocks or []:
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        parts.append(f'<script type="application/ld+json">{payload}</script>')
    return "".join(parts)


def off_str(td):
    m = int(td.total_seconds() // 60)
    sign = "+" if m >= 0 else "−"
    h, mm = divmod(abs(m), 60)
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def hours_str(minutes):
    h, m = divmod(abs(minutes), 60)
    return f"{h} 小时" + (f" {m} 分钟" if m else "") if (h or m) else "0 小时"


def diff_text(name_a, name_b, minutes):
    """minutes = a 的 UTC 偏移 − b 的 UTC 偏移。"""
    if minutes == 0:
        return f"{name_a}和{name_b}没有时差"
    return f"{name_a}比{name_b}{'快' if minutes > 0 else '慢'} {hours_str(minutes)}"


class Site:
    def __init__(self, today):
        self.cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.base = self.cfg["base_url"].rstrip("/")
        self.today = today
        self.noon = dt.datetime(today.year, today.month, today.day, 12, tzinfo=BJ)  # "当前"取今天北京时间中午
        self.city = {c[0]: c for c in CITIES}
        self.urls = []

    # ----- 时区计算 -----
    def offset(self, tz, when=None):
        return (when or self.noon).astimezone(ZoneInfo(tz)).utcoffset()

    def minutes(self, tz, when=None):
        return int(self.offset(tz, when).total_seconds() // 60)

    def transitions(self, tz, year):
        """该年 UTC 偏移变化的时刻:[(当地切换前时间, 旧偏移分钟, 新偏移分钟)]。"""
        z, out = ZoneInfo(tz), []
        t = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
        prev = t.astimezone(z).utcoffset()
        while t < end:
            n = t + dt.timedelta(days=1)
            if n.astimezone(z).utcoffset() != prev:
                lo, hi = t, n
                while hi - lo > dt.timedelta(minutes=1):
                    mid = lo + (hi - lo) / 2
                    if mid.astimezone(z).utcoffset() == prev:
                        lo = mid
                    else:
                        hi = mid
                new = hi.astimezone(z).utcoffset()
                local_before = hi.replace(tzinfo=None) + prev  # 切换瞬间按旧偏移表示的当地时间
                out.append((local_before, int(prev.total_seconds() // 60), int(new.total_seconds() // 60)))
                prev = new
            t = n
        return out

    def dst_summary(self, tz, year):
        tr = self.transitions(tz, year)
        if len(tr) != 2:
            return None
        start = next(x for x in tr if x[2] > x[1])
        stop = next(x for x in tr if x[2] < x[1])
        return start, stop

    def fmt_local(self, t):
        return f"{t.month}月{t.day}日 {t.hour}:{t.minute:02d}"

    def on_dst(self, tz, when=None):
        """生成日中午(北京时间)该时区是否处于夏令时。看当时的 dst 偏移,不依赖一年刚好两次拨钟。"""
        delta = (when or self.noon).astimezone(ZoneInfo(tz)).dst()
        return bool(delta)

    def observes_dst(self, tz, year=None):
        year = year or self.today.year
        if self.dst_summary(tz, year):
            return True
        z, prev = ZoneInfo(tz), None
        for month in range(1, 13):
            loc = dt.datetime(year, month, 15, 12, tzinfo=dt.timezone.utc).astimezone(z)
            if loc.dst():
                return True
            off = loc.utcoffset()
            if prev is not None and off != prev:
                return True
            prev = off
        return False

    def dst_phrase(self, tz):
        if self.on_dst(tz):
            return "目前为夏令时"
        if self.observes_dst(tz):
            return "目前为标准时间"
        return "不实行夏令时"

    def clock_text(self, slug):
        """时钟元素的静态文字。带 data-tz 的脚本会把它改写成实时时间。"""
        name, tz = self.city[slug][1], self.city[slug][2]
        off = off_str(self.offset(tz))
        note = self.dst_phrase(tz)
        if slug == "beijing":
            return f"北京时间 {off}，{note}"
        diff = diff_text(name, "北京时间", self.minutes(tz) - self.minutes("Asia/Shanghai"))
        return f"{diff}（{off}，{note}）"

    def date_text(self, tz):
        local = self.noon.astimezone(ZoneInfo(tz))
        week = "一二三四五六日"[local.weekday()]
        return f"{local.year}年{local.month}月{local.day}日 星期{week}"

    def city_faqs(self, slug):
        """城市页可见问答。JSON-LD 用同一段文字。"""
        name, tz = self.city[slug][1], self.city[slug][2]
        off = off_str(self.offset(tz))
        note = self.dst_phrase(tz)
        m, bj = self.minutes(tz), self.minutes("Asia/Shanghai")
        local = self.noon.astimezone(ZoneInfo(tz))
        tag = {-1: "(前一天)", 1: "(次日)"}.get((local.date() - self.today).days, "")
        q1 = f"{name}和北京时差几小时?"
        if slug == "beijing":
            a1 = f"北京和北京时间没有时差（{off}，{note}）。北京时间比协调世界时快 {hours_str(bj)}，全年不拨钟。"
            return [(q1, a1)]
        diff = diff_text(name, "北京时间", m - bj)
        a1 = f"目前{diff}（{off}，{note}）。北京时间中午 12 点时，{name}是 {local:%H:%M}{tag}。"
        if m == bj:
            how = f"{name}与北京时间相同，不用换算"
        elif m < bj:
            how = f"把北京时间减去 {hours_str(bj - m)}"
        else:
            how = f"把北京时间加上 {hours_str(m - bj)}"
        if self.observes_dst(tz):
            tail = "以上为当前时差，夏令时切换后会变化"
        else:
            tail = f"北京和{name}都不实行夏令时，时差全年不变"
        q2 = f"北京时间怎么换算成{name}时间?"
        return [(q1, a1), (q2, f"{how}（{tail}）。")]

    def faq_html(self, pairs):
        return "".join(f"<p><b>{esc(q)}</b><br>{esc(a)}</p>" for q, a in pairs)

    def crumbs(self, items):
        return {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": i, "name": name, "item": self.base + path}
                for i, (name, path) in enumerate(items, 1)
            ],
        }

    def faq_ld(self, pairs, lang):
        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "inLanguage": lang,
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in pairs
            ],
        }

    def home_ld(self, desc, name=None, url=None, lang="zh-CN"):
        name = name or self.cfg["site_name"]
        url = url or (self.base + "/")
        return [
            {"@context": "https://schema.org", "@type": "WebSite", "name": name, "url": url,
             "description": desc, "inLanguage": lang},
            {"@context": "https://schema.org", "@type": "WebApplication", "name": name, "url": url,
             "description": desc, "applicationCategory": "UtilitiesApplication", "operatingSystem": "All",
             "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CNY"}, "inLanguage": lang},
        ]

    # ----- 布局 -----
    def ad(self):
        c = self.cfg.get("adsense_client")
        if not c:
            return ""
        return (f'<div class="ad"><ins class="adsbygoogle" style="display:block" data-ad-client="{esc(c)}" data-ad-format="auto" '
                'data-full-width-responsive="true"></ins><script>(adsbygoogle=window.adsbygoogle||[]).push({});</script></div>')

    def network(self):
        sites = [s for s in self.cfg.get("network", []) if s["url"].rstrip("/") != self.cfg["base_url"].rstrip("/")]
        if not sites:
            return ""
        return "<br>更多实用工具:" + " · ".join(f'<a href="{esc(s["url"])}">{esc(s["name"])}</a>' for s in sites)

    def write(self, path, title, desc, body, ld=None):
        if not ld:
            raise ValueError(f"missing json-ld for {path}")
        cfg = self.cfg
        url = self.base + path
        ads = ""
        if cfg.get("adsense_client"):
            ads = (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
                   f'{esc(cfg["adsense_client"])}" crossorigin="anonymous"></script>')
        prefix = urllib.parse.urlparse(cfg["base_url"]).path.rstrip("/")
        og = (
            f'<meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(desc)}">'
            f'<meta property="og:url" content="{esc(url)}">'
            f'<meta property="og:type" content="website">'
            f'<meta property="og:site_name" content="{esc(cfg["site_name"])}">'
            f'<meta property="og:locale" content="zh_CN">'
        )
        page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(url)}"><!--hreflang-->{og}{ld_html(ld)}{ads}<style>{CSS}</style></head><body>
<header><a class="logo" href="/">{esc(cfg["site_name"])}</a><nav><a href="/">世界时钟</a><a href="/converter/">时间换算</a><a href="/dst/">夏令时</a>
<a href="/city/newyork/">纽约时间</a><a href="/city/london/">伦敦时间</a><a href="/city/tokyo/">东京时间</a></nav></header>
<main>{body}</main>
<footer>时区与夏令时规则依据 IANA 时区数据库,页面时钟按你设备的系统时间显示。{self.network()}</footer>
{CLOCK_JS}</body></html>""".replace('href="/', f'href="{prefix}/')
        f = OUT / path.strip("/") / "index.html"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page, encoding="utf-8")
        self.urls.append(path)

    # ----- 页面 -----
    def hour_table(self, a, b):
        """a 城市当天 0~23 点对应 b 城市的时间。"""
        za, zb = ZoneInfo(self.city[a][2]), ZoneInfo(self.city[b][2])
        local_day = self.noon.astimezone(za).date()
        rows = []
        for h in range(24):
            ta = dt.datetime(local_day.year, local_day.month, local_day.day, h, tzinfo=za)
            tb = ta.astimezone(zb)
            dd = (tb.date() - ta.date()).days
            tag = {-1: "(前一天)", 1: "(次日)"}.get(dd, "")
            rows.append(f"<tr><td>{h:02d}:00</td><td>{tb.hour:02d}:{tb.minute:02d}{tag}</td></tr>")
        return (f"<table><tr><th>{esc(self.city[a][1])}时间</th><th>{esc(self.city[b][1])}时间</th></tr>{''.join(rows)}</table>"
                f'<p class="muted">按 {local_day.year}年{local_day.month}月{local_day.day}日的时差计算。</p>')

    def dst_text(self, slug):
        _, name, tz, _, _ = self.city[slug]
        y = self.today.year
        s = self.dst_summary(tz, y)
        if not s:
            off = off_str(self.offset(tz))
            if self.on_dst(tz):
                return f"<p>{esc(name)}目前为夏令时,使用 {off}。</p>"
            if self.observes_dst(tz):
                return f"<p>{esc(name)}实行夏令时,目前为标准时间,使用 {off}。</p>"
            return f"<p>{esc(name)}目前不实行夏令时,全年使用 {off}。</p>"
        (t1, o1, n1), (t2, o2, n2) = s
        bj = self.minutes("Asia/Shanghai")
        if t1 < t2:  # 北半球:当年春季开始、秋季结束
            when = (f"{y}年夏令时从 <b>{self.fmt_local(t1)}</b>(当地时间)开始,时钟拨快 {hours_str(n1 - o1)};"
                    f"到 <b>{self.fmt_local(t2)}</b> 结束,时钟拨回 {hours_str(o2 - n2)}。")
        else:  # 南半球:夏令时跨年,当年先结束、年底再开始
            when = (f"上一轮夏令时于 {y}年<b>{self.fmt_local(t2)}</b>(当地时间)结束,时钟拨回 {hours_str(o2 - n2)};"
                    f"新一轮从 {y}年<b>{self.fmt_local(t1)}</b> 开始,时钟拨快 {hours_str(n1 - o1)}" +
                    (f",到 {y + 1}年{self.fmt_local(nxt[1][0])} 结束。" if (nxt := self.dst_summary(tz, y + 1)) else "。"))
        return (f"<p>{esc(name)}实行夏令时。{when}</p>"
                f"<p>夏令时期间({off_str(dt.timedelta(minutes=n1))}){diff_text(esc(name), '北京', n1 - bj)};"
                f"其余时间({off_str(dt.timedelta(minutes=o1))}){diff_text(esc(name), '北京', o1 - bj)}。</p>")

    def city_page(self, slug):
        _, name, tz, country, _ = self.city[slug]
        m, bj = self.minutes(tz), self.minutes("Asia/Shanghai")
        abbr = self.noon.astimezone(ZoneInfo(tz)).tzname()
        others = "".join(f'<a href="/city/{c[0]}/">{esc(c[1])}时间</a>' for c in CITIES if c[0] != slug)
        diff = diff_text(name, "北京", m - bj)
        is_bj = slug == "beijing"
        pairs = "".join(f'<a href="/diff/{p}/">{esc(t)}</a>' for p, t in self.pair_links(slug))
        faqs = self.city_faqs(slug)
        body = f"""<div class="card"><h1>{esc(name)}现在几点?{esc(name)}时间{'' if is_bj else '与北京时差'}</h1>
<div class="big" data-tz="{tz}" data-s="1">{esc(self.clock_text(slug))}</div><div class="muted" data-tz="{tz}" data-d="1">{esc(self.date_text(tz))}</div>
{'' if is_bj else f'<p>北京时间 <b data-tz="Asia/Shanghai">{esc(self.clock_text("beijing"))}</b> · 目前{esc(diff)}。</p>'}
<p class="muted">{esc(country)} · 时区 {tz} · 目前 {off_str(self.offset(tz))}({esc(abbr)})</p></div>{self.ad()}
<div class="card"><h2>夏令时</h2>{self.dst_text(slug)}</div>
{'' if is_bj else f'<div class="card"><h2>北京时间换算{esc(name)}时间</h2>{self.hour_table("beijing", slug)}</div>'}
<div class="card"><h2>常见问题</h2>{self.faq_html(faqs)}</div>
{f'<div class="card"><h2>{esc(name)}与其他城市时差</h2><div class="links">{pairs}</div></div>' if pairs else ''}
<div class="card"><h2>其他城市时间</h2><div class="links">{others}</div></div>"""
        title = (f"{name}现在几点 - {name}时间与北京时差({diff.replace(name, '').strip()})" if not is_bj
                 else "北京时间现在几点 - 北京时间在线校准")
        desc = f"{name}现在时间实时显示,{name}时区为{off_str(self.offset(tz))},{'目前' + diff + '。' if not is_bj else ''}附夏令时说明和北京时间对照表。"
        self.write(f"/city/{slug}/", title, desc, body, ld=[
            self.crumbs([("世界时间", "/"), (f"{name}时间", f"/city/{slug}/")]),
            self.faq_ld(faqs, "zh-CN"),
        ])

    def hot(self):
        return [c[0] for c in CITIES if c[4] and c[0] != "beijing"]

    def pair_links(self, slug):
        if slug not in self.hot():
            return []
        return [(f"{a}-{b}", f"{self.city[b if a == slug else a][1]}") for a, b in itertools.combinations(self.hot(), 2) if slug in (a, b)]

    def pair_page(self, a, b):
        na, nb = self.city[a][1], self.city[b][1]
        ta, tb = self.city[a][2], self.city[b][2]
        d = self.minutes(ta) - self.minutes(tb)
        body = f"""<div class="card"><h1>{esc(na)}和{esc(nb)}时差多少?</h1>
<div class="big">{diff_text(esc(na), esc(nb), d)}</div>
<p>{esc(na)}现在 <b data-tz="{ta}">{esc(self.clock_text(a))}</b> · {esc(nb)}现在 <b data-tz="{tb}">{esc(self.clock_text(b))}</b></p>
<p class="muted">{esc(na)} {off_str(self.offset(ta))} · {esc(nb)} {off_str(self.offset(tb))} · 按 {self.today.month}月{self.today.day}日计算,夏令时切换后时差可能变化。</p></div>{self.ad()}
<div class="card"><h2>{esc(na)}夏令时</h2>{self.dst_text(a)}</div>
<div class="card"><h2>{esc(nb)}夏令时</h2>{self.dst_text(b)}</div>
<div class="card"><h2>{esc(na)}时间换算{esc(nb)}时间</h2>{self.hour_table(a, b)}</div>
<div class="card"><div class="links"><a href="/city/{a}/">{esc(na)}时间</a><a href="/city/{b}/">{esc(nb)}时间</a><a href="/converter/">时间换算器</a></div></div>"""
        self.write(f"/diff/{a}-{b}/", f"{na}和{nb}时差多少小时 - {na}{nb}时间换算",
                   f"{diff_text(na, nb, d)}(按{self.today.month}月{self.today.day}日计算)。附两地夏令时说明和时间对照表。", body,
                   ld=[self.crumbs([("世界时间", "/"), (f"{na}和{nb}时差", f"/diff/{a}-{b}/")])])

    def home(self):
        clocks = "".join(
            f'<a href="/city/{c[0]}/"><small>{esc(c[1])}</small><b data-tz="{c[2]}">{esc(self.clock_text(c[0]))}</b><small>{off_str(self.offset(c[2]))}</small></a>'
            for c in CITIES)
        desc = "实时显示北京时间和世界 49 个主要城市当地时间,查询各国与北京的时差、夏令时和时间换算。"
        body = f"""<div class="card"><h1>世界时间 - 北京时间现在几点</h1><div class="big" data-tz="Asia/Shanghai" data-s="1">{esc(self.clock_text("beijing"))}</div>
<div class="muted" data-tz="Asia/Shanghai" data-d="1">{esc(self.date_text("Asia/Shanghai"))}</div></div>{self.ad()}
<div class="card"><h2>世界主要城市时间</h2><div class="clocks">{clocks}</div></div>"""
        self.write("/", f"{self.cfg['site_name']} - 世界时间、各国时差查询", desc, body, ld=self.home_ld(desc))

    def dst_page(self):
        y = self.today.year
        rows = []
        for slug, name, tz, country, _ in CITIES:
            s = self.dst_summary(tz, y)
            if s:
                rows.append(f'<tr><td><a href="/city/{slug}/">{esc(name)}</a></td><td>{self.fmt_local(s[0][0])}</td><td>{self.fmt_local(s[1][0])}</td></tr>')
        none = "、".join(c[1] for c in CITIES if not self.dst_summary(c[2], y))
        body = f"""<div class="card"><h1>{y}年夏令时时间表</h1><p>夏令时是在夏季把时钟拨快 1 小时,冬季再拨回来。北半球一般在 3 月开始、10～11 月结束;南半球(如悉尼、奥克兰)正好相反。</p></div>{self.ad()}
<div class="card"><table><tr><th>城市</th><th>开始(当地时间)</th><th>结束(当地时间)</th></tr>{"".join(rows)}</table>
<p class="muted">南半球城市(悉尼、墨尔本、奥克兰等)的夏令时跨年:表中"开始"是当年年底开始的一轮,"结束"是上一年开始、当年春季结束的一轮。</p></div>
<div class="card"><h2>不实行夏令时的城市</h2><p>{esc(none)}。中国目前不实行夏令时。</p></div>"""
        self.write("/dst/", f"{y}年夏令时什么时候开始和结束 - 美国、欧洲、澳洲夏令时时间表",
                   f"{y}年各国夏令时开始和结束时间:美国、英国、欧洲、澳大利亚、新西兰等主要城市夏令时时间表。", body,
                   ld=[self.crumbs([("世界时间", "/"), (f"{y}年夏令时", "/dst/")])])

    def converter_page(self):
        opts = "".join(f'<option value="{c[2]}"{" selected" if c[0] == "beijing" else ""}>{esc(c[1])}</option>' for c in CITIES)
        names = json.dumps([[c[1], c[2], c[0]] for c in CITIES], ensure_ascii=False)
        js = """<script>(function(){var C=__C__,z=document.getElementById('z'),t=document.getElementById('t'),o=document.getElementById('o');
function parts(d,tz){var p={};new Intl.DateTimeFormat('en-US',{timeZone:tz,hourCycle:'h23',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).formatToParts(d).forEach(function(x){p[x.type]=x.value});return p}
function toUtc(s,tz){var a=s.split(/[-T:]/).map(Number),g=Date.UTC(a[0],a[1]-1,a[2],a[3],a[4]);for(var i=0;i<2;i++){var p=parts(new Date(g),tz);
var asUtc=Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute);g+=Date.UTC(a[0],a[1]-1,a[2],a[3],a[4])-asUtc}return new Date(g)}
function run(){if(!t.value)return;var d=toUtc(t.value,z.value);o.innerHTML=C.map(function(c){var p=parts(d,c[1]);
return '<tr><td><a href="/city/'+c[2]+'/">'+c[0]+'</a></td><td>'+p.month+'月'+p.day+'日 '+p.hour+':'+p.minute+'</td></tr>'}).join('')}
var n=parts(new Date(),z.value);t.value=n.year+'-'+n.month+'-'+n.day+'T'+n.hour+':'+n.minute;z.onchange=t.oninput=run;run();})();</script>""".replace("__C__", names)
        body = f"""<div class="card"><h1>时区时间换算器</h1><p class="muted">选择城市和时间,查看同一时刻世界各地的当地时间(自动考虑夏令时)。</p>
<div class="form"><label>城市<select id="z">{opts}</select></label><label>当地时间<input id="t" type="datetime-local"></label></div></div>{self.ad()}
<div class="card"><table><tr><th>城市</th><th>当地时间</th></tr><tbody id="o"></tbody></table></div>{js}"""
        self.write("/converter/", "时区换算器 - 北京时间换算世界各地时间", "在线时区时间换算,输入任意城市的时间,换算成世界各地对应的当地时间,自动处理夏令时。", body,
                   ld=[self.crumbs([("世界时间", "/"), ("时间换算", "/converter/")])])

    def build(self):
        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir()
        self.home()
        for c in CITIES:
            self.city_page(c[0])
        for a, b in itertools.combinations(self.hot(), 2):
            self.pair_page(a, b)
        self.dst_page()
        self.converter_page()
        base = self.cfg["base_url"].rstrip("/")
        sm = "".join(f"<url><loc>{esc(base + u)}</loc><lastmod>{self.today}</lastmod></url>" for u in self.urls)
        (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>', encoding="utf-8")
        (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
        if self.cfg.get("adsense_client"):
            (OUT / "ads.txt").write_text(f"google.com, {self.cfg['adsense_client'].replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n", encoding="utf-8")
        print(f"生成 {len(self.urls)} 个页面,今天 {self.today}")
        validate_output(require_hreflang=False)


def validate_output(require_hreflang=False):
    """检查 dist:JSON-LD 可解析且含必填字段,OG 与 canonical 一致,时钟不是 --:--。"""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    base = cfg["base_url"].rstrip("/")
    site_name = cfg["site_name"]
    files = sorted(OUT.rglob("index.html"))
    errors = []

    def err(msg):
        if len(errors) < 40:
            errors.append(msg)

    robots = OUT / "robots.txt"
    if not robots.exists() or "Allow: /" not in robots.read_text(encoding="utf-8") or f"Sitemap: {base}/sitemap.xml" not in robots.read_text(encoding="utf-8"):
        err("robots.txt missing Allow or Sitemap")
    sm_path = OUT / "sitemap.xml"
    locs = []
    if not sm_path.exists():
        err("missing sitemap.xml")
    else:
        sm = sm_path.read_text(encoding="utf-8")
        locs = re.findall(r"<loc>([^<]+)</loc>", sm)
        if sm.count("<lastmod>") != len(locs):
            err("sitemap lastmod count != loc count")
        if len(locs) != len(set(locs)):
            err("sitemap has duplicate locs")
    canonicals = set()
    pages = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(OUT).as_posix()
        if "--:--" in text:
            err(f"{rel} still contains --:--")
        if re.search(r'data-tz="[^"]*"[^>]*>\s*</', text):
            err(f"{rel} has an empty data-tz element")
        head, _, _ = text.partition("</head>")
        can_m = re.search(r'<link rel="canonical" href="([^"]*)">', text)
        if not can_m:
            err(f"{rel} missing canonical")
            continue
        canonical = can_m.group(1)
        canonicals.add(canonical)
        if '<html lang="zh-CN">' in text:
            lang = "zh-CN"
        elif '<html lang="en">' in text:
            lang = "en"
        else:
            lang = ""
            err(f"{rel} missing html lang")
        title_m = re.search(r"<title>([^<]*)</title>", text)
        desc_m = re.search(r'<meta name="description" content="([^"]*)">', text)
        og = dict(re.findall(r'<meta property="(og:[^"]+)" content="([^"]*)">', text))
        for key in ("og:title", "og:description", "og:url", "og:type", "og:site_name", "og:locale"):
            if key not in og:
                err(f"{rel} missing {key}")
        if title_m and og.get("og:title") != title_m.group(1):
            err(f"{rel} og:title != title")
        if desc_m and og.get("og:description") != desc_m.group(1):
            err(f"{rel} og:description != description")
        if og.get("og:url") != canonical:
            err(f"{rel} og:url != canonical")
        if og.get("og:type") != "website":
            err(f"{rel} og:type != website")
        if lang == "zh-CN":
            if og.get("og:locale") != "zh_CN":
                err(f"{rel} og:locale")
            if og.get("og:site_name") != site_name:
                err(f"{rel} og:site_name")
            if "夏令时" not in text:
                err(f"{rel} missing DST note")
        elif lang == "en":
            if og.get("og:locale") != "en_US":
                err(f"{rel} og:locale")
            if og.get("og:site_name") != "World Time":
                err(f"{rel} og:site_name")
            if "daylight saving" not in text.lower():
                err(f"{rel} missing DST note")
        if "application/ld+json" not in head:
            err(f"{rel} json-ld not in head")
        parsed = []
        for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                err(f"{rel} invalid json-ld: {e}")
                continue
            if data.get("@context") != "https://schema.org" or not data.get("@type"):
                err(f"{rel} json-ld missing @context or @type")
            parsed.append(data)
        if not parsed:
            err(f"{rel} missing json-ld")
        types = [p.get("@type") for p in parsed]
        visible = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
        visible = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible, flags=re.I | re.S)
        visible = html.unescape(re.sub(r"<[^>]+>", " ", visible))
        home = canonical in (base + "/", base + "/en/")
        city_zh = "/city/" in canonical
        city_en = "/en/time/" in canonical
        if home:
            if "WebSite" not in types or "WebApplication" not in types:
                err(f"{rel} home missing WebSite/WebApplication")
            if "BreadcrumbList" in types or "FAQPage" in types:
                err(f"{rel} home should not have breadcrumb/faq schema")
        else:
            if "BreadcrumbList" not in types:
                err(f"{rel} missing BreadcrumbList")
            if "WebSite" in types or "WebApplication" in types:
                err(f"{rel} inner page should not have WebSite/WebApplication")
        if (city_zh or city_en) and "FAQPage" not in types:
            err(f"{rel} city page missing FAQPage")
        if not (city_zh or city_en) and "FAQPage" in types:
            err(f"{rel} unexpected FAQPage")
        for p in parsed:
            kind = p.get("@type")
            if kind == "WebApplication":
                offers = p.get("offers") or {}
                if offers.get("@type") != "Offer" or str(offers.get("price")) != "0" or offers.get("priceCurrency") != "CNY":
                    err(f"{rel} offers must be price 0 CNY")
                if p.get("applicationCategory") != "UtilitiesApplication" or p.get("operatingSystem") != "All":
                    err(f"{rel} applicationCategory/operatingSystem")
            if kind in ("WebSite", "WebApplication"):
                if p.get("url") != canonical:
                    err(f"{rel} {kind} url != canonical")
                if desc_m and p.get("description") != html.unescape(desc_m.group(1)):
                    err(f"{rel} {kind} description mismatch")
                if not p.get("name"):
                    err(f"{rel} {kind} missing name")
            if kind == "BreadcrumbList":
                items = p.get("itemListElement") or []
                if len(items) < 2:
                    err(f"{rel} breadcrumb needs >= 2 items")
                elif [it.get("position") for it in items] != list(range(1, len(items) + 1)):
                    err(f"{rel} breadcrumb positions")
                elif items[-1].get("item") != canonical:
                    err(f"{rel} breadcrumb last item != canonical")
                elif items[0].get("item") not in (base + "/", base + "/en/"):
                    err(f"{rel} breadcrumb root")
                for it in items:
                    if not it.get("name") or not str(it.get("item", "")).startswith("https://"):
                        err(f"{rel} breadcrumb item name/url")
            if kind == "FAQPage":
                entities = p.get("mainEntity") or []
                if not entities:
                    err(f"{rel} empty FAQ")
                for q in entities:
                    question = q.get("name") or ""
                    answer = ((q.get("acceptedAnswer") or {}).get("text")) or ""
                    if not question or not answer or "<" in question or "<" in answer:
                        err(f"{rel} bad faq text")
                        continue
                    if question not in visible or answer not in visible:
                        err(f"{rel} faq not visible: {question[:48]}")
                if city_zh and not any("和北京时差几小时" in (q.get("name") or "") for q in entities):
                    err(f"{rel} missing 和北京时差几小时 question")
                if city_en and not any("Beijing" in (q.get("name") or "") for q in entities):
                    err(f"{rel} missing Beijing FAQ")
        need_ref = home or city_zh or city_en or "/diff/" in canonical or "/en/difference/" in canonical
        if need_ref:
            if lang == "zh-CN" and "北京" not in visible:
                err(f"{rel} missing Beijing comparison")
            if lang == "en" and "Beijing" not in visible:
                err(f"{rel} missing Beijing comparison")
            if "UTC" not in visible:
                err(f"{rel} missing UTC offset")
        if require_hreflang:
            if "<!--hreflang-->" in text:
                err(f"{rel} unfilled hreflang")
            alt_list = re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', text)
            if len(alt_list) != len(dict(alt_list)):
                err(f"{rel} duplicate hreflang")
            alts = dict(alt_list)
            if "x-default" not in alts:
                err(f"{rel} missing x-default")
            if lang == "zh-CN" and alts.get("zh-CN") != canonical:
                err(f"{rel} zh-CN hreflang not self")
            if lang == "en" and alts.get("en") != canonical:
                err(f"{rel} en hreflang not self")
            if "zh-CN" in alts:
                if alts.get("x-default") != alts["zh-CN"]:
                    err(f"{rel} x-default should be the zh-CN url")
            elif alts.get("x-default") != canonical:
                err(f"{rel} x-default should be self")
            pages[canonical] = alts
    if set(locs) != canonicals:
        err(f"sitemap mismatch missing {sorted(canonicals - set(locs))[:3]} extra {sorted(set(locs) - canonicals)[:3]}")
    if require_hreflang:
        for canonical, alts in pages.items():
            for code, href in alts.items():
                if code == "x-default":
                    if href not in pages:
                        err(f"{canonical} x-default target missing {href}")
                    continue
                if href not in pages:
                    err(f"{canonical} hreflang {code} target missing {href}")
                elif canonical not in pages[href].values():
                    err(f"{href} does not link back to {canonical}")
    if errors:
        raise SystemExit("SEO check failed:\n" + "\n".join(errors))


if __name__ == "__main__":
    if "--today" in sys.argv:
        today = dt.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    else:
        today = dt.datetime.now(BJ).date()
    Site(today).build()
