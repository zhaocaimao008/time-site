#!/usr/bin/env python3
"""生成英文版世界时间页面到 dist/en/,并给中英文对应页面互相加上 hreflang 和语言切换链接。

需要在 build.py 之后运行;时区计算复用 build.Site。
"""
import datetime as dt
import itertools
import json
import sys
from zoneinfo import ZoneInfo

from build import BJ, CLOCK_JS, CSS, OUT, Site, esc, off_str
from cities import CITIES

EN = {
    "beijing": ("Beijing", "China"), "hongkong": ("Hong Kong", "China"), "taipei": ("Taipei", "Taiwan"),
    "tokyo": ("Tokyo", "Japan"), "seoul": ("Seoul", "South Korea"), "singapore": ("Singapore", "Singapore"),
    "bangkok": ("Bangkok", "Thailand"), "kualalumpur": ("Kuala Lumpur", "Malaysia"), "jakarta": ("Jakarta", "Indonesia"),
    "manila": ("Manila", "Philippines"), "hochiminh": ("Ho Chi Minh City", "Vietnam"), "phnompenh": ("Phnom Penh", "Cambodia"),
    "yangon": ("Yangon", "Myanmar"), "ulaanbaatar": ("Ulaanbaatar", "Mongolia"), "newdelhi": ("New Delhi", "India"),
    "kathmandu": ("Kathmandu", "Nepal"), "karachi": ("Karachi", "Pakistan"), "dubai": ("Dubai", "United Arab Emirates"),
    "doha": ("Doha", "Qatar"), "riyadh": ("Riyadh", "Saudi Arabia"), "tehran": ("Tehran", "Iran"), "istanbul": ("Istanbul", "Turkey"),
    "moscow": ("Moscow", "Russia"), "london": ("London", "United Kingdom"), "paris": ("Paris", "France"), "berlin": ("Berlin", "Germany"),
    "rome": ("Rome", "Italy"), "madrid": ("Madrid", "Spain"), "amsterdam": ("Amsterdam", "Netherlands"), "zurich": ("Zurich", "Switzerland"),
    "cairo": ("Cairo", "Egypt"), "johannesburg": ("Johannesburg", "South Africa"), "newyork": ("New York", "United States"),
    "washington": ("Washington, D.C.", "United States"), "chicago": ("Chicago", "United States"), "houston": ("Houston", "United States"),
    "losangeles": ("Los Angeles", "United States"), "sanfrancisco": ("San Francisco", "United States"), "seattle": ("Seattle", "United States"),
    "honolulu": ("Honolulu", "United States"), "toronto": ("Toronto", "Canada"), "vancouver": ("Vancouver", "Canada"),
    "mexicocity": ("Mexico City", "Mexico"), "saopaulo": ("São Paulo", "Brazil"), "buenosaires": ("Buenos Aires", "Argentina"),
    "sydney": ("Sydney", "Australia"), "melbourne": ("Melbourne", "Australia"), "perth": ("Perth", "Australia"), "auckland": ("Auckland", "New Zealand"),
}
# 英文版城市页里对比的参照城市
REF = ["newyork", "losangeles", "london", "beijing", "tokyo"]


def hrs(minutes):
    h, m = divmod(abs(minutes), 60)
    s = f"{h} hour{'s' if h != 1 else ''}" if h else ""
    if m:
        s += (" " if s else "") + f"{m} minutes"
    return s or "0 hours"


def diff_en(a, b, minutes):
    if minutes == 0:
        return f"{a} and {b} are in the same time"
    return f"{a} is {hrs(minutes)} {'ahead of' if minutes > 0 else 'behind'} {b}"


def md(t):
    return f"{t:%B} {t.day}, {t.hour}:{t.minute:02d}"


class SiteEn(Site):
    def __init__(self, today):
        super().__init__(today)
        self.alt = {}
        self.base = self.cfg["base_url"].rstrip("/")
        self.prefix = self.base.split("://", 1)[-1].partition("/")[2]
        self.prefix = "/" + self.prefix if self.prefix else ""

    def name(self, slug):
        return EN[slug][0]

    def write(self, path, title, desc, body, zh=None):
        ads = ""
        if self.cfg.get("adsense_client"):
            ads = (f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
                   f'{esc(self.cfg["adsense_client"])}" crossorigin="anonymous"></script>')
        alt = ""
        if zh:
            self.alt[path] = zh
            alt = (f'<link rel="alternate" hreflang="en" href="{esc(self.base + path)}">'
                   f'<link rel="alternate" hreflang="zh-CN" href="{esc(self.base + zh)}">'
                   f'<link rel="alternate" hreflang="x-default" href="{esc(self.base + path)}">')
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(self.base + path)}">{alt}{ads}<style>{CSS}</style></head><body>
<header><a class="logo" href="/en/">World Time</a><nav><a href="/en/">World Clock</a><a href="/en/converter/">Time Zone Converter</a><a href="/en/dst/">Daylight Saving</a>
<a href="/en/time/newyork/">New York</a><a href="/en/time/london/">London</a><a href="/en/time/tokyo/">Tokyo</a><a href="{zh or '/'}" hreflang="zh-CN">中文</a></nav></header>
<main>{body}</main>
<footer>Time zone and daylight saving rules from the IANA Time Zone Database. Clocks use your device's time.</footer>
{CLOCK_JS.replace("'zh-CN'", "'en-US'")}</body></html>""".replace('href="/', f'href="{self.prefix}/')
        f = OUT / path.strip("/") / "index.html"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page, encoding="utf-8")
        self.urls.append(path)

    # ----- 组件 -----
    def dst_en(self, slug):
        tz, name, y = self.city[slug][2], self.name(slug), self.today.year
        s = self.dst_summary(tz, y)
        if not s:
            return f"<p>{esc(name)} does not observe daylight saving time. It stays on {off_str(self.offset(tz))} all year.</p>"
        (t1, o1, n1), (t2, o2, n2) = s
        if t1 < t2:
            when = (f"In {y}, daylight saving time starts on <b>{md(t1)}</b> (local time), when clocks go forward {hrs(n1 - o1)}, "
                    f"and ends on <b>{md(t2)}</b>, when clocks go back {hrs(o2 - n2)}.")
        else:
            nxt = self.dst_summary(tz, y + 1)
            when = (f"Daylight saving time ended on <b>{md(t2)}, {y}</b> (local time) and starts again on <b>{md(t1)}, {y}</b>"
                    + (f", ending on {md(nxt[1][0])}, {y + 1}." if nxt else "."))
        return (f"<p>{esc(name)} observes daylight saving time. {when}</p>"
                f"<p>Standard time is {off_str(dt.timedelta(minutes=o1))}; daylight time is {off_str(dt.timedelta(minutes=n1))}.</p>")

    def hour_table_en(self, a, b):
        za, zb = ZoneInfo(self.city[a][2]), ZoneInfo(self.city[b][2])
        day = self.noon.astimezone(za).date()
        rows = []
        for h in range(24):
            ta = dt.datetime(day.year, day.month, day.day, h, tzinfo=za)
            tb = ta.astimezone(zb)
            tag = {-1: " (previous day)", 1: " (next day)"}.get((tb.date() - ta.date()).days, "")
            rows.append(f"<tr><td>{ta:%I:%M %p}</td><td>{tb:%I:%M %p}{tag}</td></tr>")
        return (f"<table><tr><th>{esc(self.name(a))}</th><th>{esc(self.name(b))}</th></tr>{''.join(rows)}</table>"
                f'<p class="muted">Based on the time difference on {day:%B} {day.day}, {day.year}.</p>')

    # ----- 页面 -----
    def city_en(self, slug):
        tz, name, country = self.city[slug][2], *EN[slug]
        m = self.minutes(tz)
        abbr = self.noon.astimezone(ZoneInfo(tz)).tzname()
        rows = "".join(f'<tr><td><a href="/en/time/{r}/">{esc(self.name(r))}</a></td><td>{esc(diff_en(name, self.name(r), m - self.minutes(self.city[r][2])))}</td></tr>'
                       for r in REF if r != slug)
        others = "".join(f'<a href="/en/time/{c[0]}/">{esc(self.name(c[0]))}</a>' for c in CITIES if c[0] != slug)
        pairs = "".join(f'<a href="/en/difference/{a}-{b}/">{esc(self.name(b if a == slug else a))}</a>'
                        for a, b in itertools.combinations(self.hot_en(), 2) if slug in (a, b))
        body = f"""<div class="card"><h1>Current local time in {esc(name)}, {esc(country)}</h1>
<div class="big" data-tz="{tz}" data-s="1">--:--:--</div><div class="muted" data-tz="{tz}" data-d="1"></div>
<p class="muted">Time zone: {tz} · currently {off_str(self.offset(tz))} ({esc(abbr)})</p></div>{self.ad()}
<div class="card"><h2>Time difference</h2><table>{rows}</table></div>
<div class="card"><h2>Daylight saving time</h2>{self.dst_en(slug)}</div>
{'' if slug == 'newyork' else f'<div class="card"><h2>{esc(name)} time vs New York time</h2>{self.hour_table_en(slug, "newyork")}</div>'}
{f'<div class="card"><h2>Time difference with</h2><div class="links">{pairs}</div></div>' if pairs else ''}
<div class="card"><h2>Other cities</h2><div class="links">{others}</div></div>"""
        self.write(f"/en/time/{slug}/", f"Current Time in {name}, {country} – {name} Time Zone ({off_str(self.offset(tz))})",
                   f"Current local time in {name}, {country}. Time zone {off_str(self.offset(tz))}, daylight saving dates and time difference with New York, London and more.",
                   body, f"/city/{slug}/")

    def hot_en(self):
        return ["beijing"] + self.hot()

    def pair_en(self, a, b):
        na, nb, ta, tb = self.name(a), self.name(b), self.city[a][2], self.city[b][2]
        d = self.minutes(ta) - self.minutes(tb)
        zh = f"/diff/{a}-{b}/" if (OUT / "diff" / f"{a}-{b}" / "index.html").exists() else None
        body = f"""<div class="card"><h1>Time difference between {esc(na)} and {esc(nb)}</h1>
<div class="big">{esc(diff_en(na, nb, d))}</div>
<p>{esc(na)} now: <b data-tz="{ta}">--:--</b> · {esc(nb)} now: <b data-tz="{tb}">--:--</b></p>
<p class="muted">{esc(na)} {off_str(self.offset(ta))} · {esc(nb)} {off_str(self.offset(tb))} · as of {self.today:%B} {self.today.day}; the difference can change when daylight saving time starts or ends.</p></div>{self.ad()}
<div class="card"><h2>{esc(na)} to {esc(nb)} time conversion</h2>{self.hour_table_en(a, b)}</div>
<div class="card"><h2>Daylight saving in {esc(na)}</h2>{self.dst_en(a)}</div>
<div class="card"><h2>Daylight saving in {esc(nb)}</h2>{self.dst_en(b)}</div>
<div class="card"><div class="links"><a href="/en/time/{a}/">{esc(na)} time</a><a href="/en/time/{b}/">{esc(nb)} time</a><a href="/en/converter/">Time zone converter</a></div></div>"""
        self.write(f"/en/difference/{a}-{b}/", f"{na} to {nb} Time Difference – {na} vs {nb} Time",
                   f"{diff_en(na, nb, d)} (as of {self.today:%B} {self.today.day}). Hour-by-hour conversion table and daylight saving dates.", body, zh)

    def home_en(self):
        clocks = "".join(f'<a href="/en/time/{c[0]}/"><small>{esc(self.name(c[0]))}</small><b data-tz="{c[2]}">--:--</b><small>{off_str(self.offset(c[2]))}</small></a>' for c in CITIES)
        body = f"""<div class="card"><h1>World Clock – Current Time Around the World</h1><p class="muted">Live local time in {len(CITIES)} major cities, with time differences and daylight saving dates.</p></div>{self.ad()}
<div class="card"><div class="clocks">{clocks}</div></div>"""
        self.write("/en/", "World Clock – Current Local Time in Cities Worldwide", "Live world clock with the current local time in New York, London, Tokyo, Sydney and other major cities, plus time zone differences.", body, "/")

    def dst_en_page(self):
        y = self.today.year
        rows = "".join(f'<tr><td><a href="/en/time/{c[0]}/">{esc(self.name(c[0]))}</a></td><td>{md(s[0][0])}</td><td>{md(s[1][0])}</td></tr>'
                       for c in CITIES if (s := self.dst_summary(c[2], y)))
        body = f"""<div class="card"><h1>Daylight Saving Time {y}: Start and End Dates</h1><p>Clocks go forward one hour in spring and back in autumn. In the Northern Hemisphere DST usually starts in March and ends in October or November; in the Southern Hemisphere (Sydney, Auckland) it is the reverse.</p></div>{self.ad()}
<div class="card"><table><tr><th>City</th><th>Starts (local)</th><th>Ends (local)</th></tr>{rows}</table>
<p class="muted">For Southern Hemisphere cities, "Starts" is the period beginning late in {y} and "Ends" is the period that began the previous year.</p></div>"""
        self.write("/en/dst/", f"Daylight Saving Time {y} – When Does DST Start and End?", f"{y} daylight saving time start and end dates for the US, UK, Europe, Australia and New Zealand.", body, "/dst/")

    def converter_en(self):
        opts = "".join(f'<option value="{c[2]}"{" selected" if c[0] == "newyork" else ""}>{esc(self.name(c[0]))}</option>' for c in CITIES)
        names = json.dumps([[self.name(c[0]), c[2], c[0]] for c in CITIES], ensure_ascii=False)
        js = """<script>(function(){var C=__C__,z=document.getElementById('z'),t=document.getElementById('t'),o=document.getElementById('o');
function parts(d,tz){var p={};new Intl.DateTimeFormat('en-US',{timeZone:tz,hourCycle:'h23',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).formatToParts(d).forEach(function(x){p[x.type]=x.value});return p}
function toUtc(s,tz){var a=s.split(/[-T:]/).map(Number),g=Date.UTC(a[0],a[1]-1,a[2],a[3],a[4]);for(var i=0;i<2;i++){var p=parts(new Date(g),tz);
g+=Date.UTC(a[0],a[1]-1,a[2],a[3],a[4])-Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute)}return new Date(g)}
function run(){if(!t.value)return;var d=toUtc(t.value,z.value);o.innerHTML=C.map(function(c){
return '<tr><td><a href="/en/time/'+c[2]+'/">'+c[0]+'</a></td><td>'+d.toLocaleString('en-US',{timeZone:c[1],month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+'</td></tr>'}).join('')}
var n=parts(new Date(),z.value);t.value=n.year+'-'+n.month+'-'+n.day+'T'+n.hour+':'+n.minute;z.onchange=t.oninput=run;run();})();</script>""".replace("__C__", names)
        body = f"""<div class="card"><h1>Time Zone Converter</h1><p class="muted">Pick a city and a local time to see the same moment around the world. Daylight saving time is handled automatically.</p>
<div class="form"><label>City<select id="z">{opts}</select></label><label>Local time<input id="t" type="datetime-local"></label></div></div>{self.ad()}
<div class="card"><table><tr><th>City</th><th>Local time</th></tr><tbody id="o"></tbody></table></div>{js}"""
        self.write("/en/converter/", "Time Zone Converter – Convert Time Between Cities", "Convert a time in one city to local times worldwide, with daylight saving time handled automatically.", body, "/converter/")

    def link_chinese(self):
        for en, zh in self.alt.items():
            f = OUT / zh.strip("/") / "index.html"
            s = f.read_text(encoding="utf-8")
            if 'hreflang="en"' in s:
                continue  # 一个中文页只关联一个英文页
            s = s.replace("<style>", f'<link rel="alternate" hreflang="zh-CN" href="{esc(self.base + zh)}">'
                                     f'<link rel="alternate" hreflang="en" href="{esc(self.base + en)}"><style>', 1)
            s = s.replace("</nav></header>", f'<a href="{self.prefix}{en}" hreflang="en">English</a></nav></header>', 1)
            f.write_text(s, encoding="utf-8")

    def build_en(self):
        self.urls = []
        self.home_en()
        for c in CITIES:
            self.city_en(c[0])
        for a, b in itertools.combinations(self.hot_en(), 2):
            self.pair_en(a, b)
        self.dst_en_page()
        self.converter_en()
        self.link_chinese()
        sm = OUT / "sitemap.xml"
        extra = "".join(f"<url><loc>{esc(self.base + u)}</loc><lastmod>{self.today}</lastmod></url>" for u in self.urls)
        sm.write_text(sm.read_text(encoding="utf-8").replace("</urlset>", extra + "</urlset>"), encoding="utf-8")
        print(f"英文页面 {len(self.urls)} 个,其中 {len(self.alt)} 个有对应中文页")


if __name__ == "__main__":
    if "--today" in sys.argv:
        today = dt.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    else:
        today = dt.datetime.now(BJ).date()
    SiteEn(today).build_en()
