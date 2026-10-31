#!/usr/bin/env python3
"""比例尺 to-scale 助手：算比例、校验配置、生成页面。只用标准库。

用法：
  python3 toscale.py suggest --length 384400 --bodies 12742 3474 --unit 公里
  python3 toscale.py suggest --length 100 --unit 米            # 堆叠：最高那一项
  python3 toscale.py suggest --count 112503120                  # 点阵：所有分组的总量
  python3 toscale.py build config.json -o page.html             # 生成完整网页
  python3 toscale.py build config.json -o page.html --fragment  # 生成 Claude Artifact 用的片段
"""
import argparse, html, json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "engine.html")
MAX_PX = 8_000_000          # 浏览器能稳定处理的长度上限（Chrome 约 3,300 万，Firefox 约 1,700 万，留足余量）
PHONE_BODY_PX = 340         # 手机屏宽减去边距后，起点/终点物体能完整显示的最大直径
SCREEN_PX = 800             # 粗略按一屏 800 像素估算滚动距离
MAX_DOTS = 2_000_000


def fmt(n):
    n = float(n)
    a = abs(n)
    if a >= 1e12: return f"{n/1e12:.2f}".rstrip("0").rstrip(".") + " 万亿"
    if a >= 1e8: return f"{n/1e8:.2f}".rstrip("0").rstrip(".") + " 亿"
    if a >= 1e4: return f"{n/1e4:.1f}".rstrip("0").rstrip(".") + " 万"
    if a >= 1 or a == 0: return f"{n:,.2f}".rstrip("0").rstrip(".")
    return f"{n:.3g}"


def nice_steps(lo, hi):
    """lo..hi 之间所有 1/2/5×10^k 的数。"""
    out = []
    k = math.floor(math.log10(lo)) - 1
    while True:
        for m in (1, 2, 5):
            v = m * 10 ** k
            if v > hi: return out
            if v >= lo: out.append(v)
        k += 1


def suggest(a):
    if a.count:
        print(f"总量 {fmt(a.count)}。点阵建议：总点数在 2 万到 30 万之间最有冲击力，手机上也画得动。")
        for per in nice_steps(max(a.count / 2_000_000, 1e-9), a.count):
            dots = a.count / per
            if 5_000 <= dots <= 600_000:
                mark = "  ← 推荐" if 20_000 <= dots <= 300_000 else ""
                print(f"  1 个点 = {fmt(per):>10}  →  {dots:>12,.0f} 个点{mark}")
        return
    L = a.length
    bodies = a.bodies or []
    big = max(bodies) if bodies else 0
    unit = a.unit or ""
    print(f"长度 {fmt(L)} {unit}" + (f"，最大物体 {fmt(big)} {unit}" if big else ""))
    lo = max(L / MAX_PX, (big / PHONE_BODY_PX) if big else 0, 1e-12)
    rows = []
    for per in nice_steps(lo, L):
        total = L / per + (big / per if big else 0)
        screens = total / SCREEN_PX
        if 1.5 <= screens <= 400: rows.append((per, total, screens))
    rows = rows[-8:]
    print("  每像素代表        总长(像素)    约几屏    最大物体(像素)  说明")
    for per, total, screens in rows:
        bp = big / per if big else 0
        notes = []
        if bodies and bp < 4: notes.append("物体小于 4 像素，几乎看不见")
        if 3 <= screens <= 60: notes.append("滚起来刚好")
        elif screens > 200: notes.append("很长：适合就是要让人滚到手酸的题目")
        print(f"  1 像素 = {fmt(per):>8} {unit:<3} {total:>12,.0f} {screens:>9,.1f} {bp:>14,.1f}  {'；'.join(notes)}")
    print("提示：物体大小和距离必须用同一个比例；想让起点物体在手机上完整显示，它的直径要 ≤ 340 像素。")


def validate(cfg):
    errs, warns, summary = [], [], []
    if not isinstance(cfg, dict): return ["配置必须是一个 JSON 对象"], [], []
    if not cfg.get("title"): errs.append("缺少 title")
    scenes = cfg.get("scenes") or []
    if not scenes: errs.append("scenes 不能为空")
    if not cfg.get("sources"): warns.append("没有 sources：只要用了查来的数字，就要写来源")
    for i, s in enumerate(scenes, 1):
        t = s.get("type")
        tag = f"场景 {i}（{t}）"
        if t == "ruler":
            per, L = float(s.get("per") or 0), float(s.get("length") or 0)
            if per <= 0 or L <= 0: errs.append(f"{tag}：需要 per > 0、length > 0"); continue
            r1 = float((s.get("from") or {}).get("size") or 0) / per
            r2 = float((s.get("to") or {}).get("size") or 0) / per
            H = 110 + r1 / 2 + L / per + r2 / 2 + 120
            if H > MAX_PX: errs.append(f"{tag}：总长 {H:,.0f} 像素，超过 {MAX_PX:,}。把 per 调大。")
            for nm, px in (("from", r1), ("to", r2)):
                if px > PHONE_BODY_PX: warns.append(f"{tag}：{nm} 物体直径 {px:,.0f} 像素，手机上会超出屏幕")
            for m in s.get("marks") or []:
                at = (float((s.get("from") or {}).get("size") or 0) / 2 + float(m["alt"])) if m.get("alt") is not None else float(m.get("at", -1))
                if not (0 <= at <= L): errs.append(f"{tag}：标记「{m.get('text','')}」位置 {at} 超出 0–{L}")
            summary.append(f"{tag}：总长 {H:,.0f} 像素，约 {H/SCREEN_PX:,.1f} 屏")
        elif t == "stack":
            per = float(s.get("per") or 0); items = s.get("items") or []
            if per <= 0 or not items: errs.append(f"{tag}：需要 per > 0 和 items"); continue
            mx = max(float(x.get("value") or 0) for x in items)
            H = mx / per + 160
            if H > MAX_PX: errs.append(f"{tag}：高度 {H:,.0f} 像素，超过上限")
            elif H > 40_000: warns.append(f"{tag}：高度 {H:,.0f} 像素（约 {H/SCREEN_PX:.0f} 屏），确认这是你想要的长度")
            for x in items:
                if float(x.get("value") or 0) / per < 1: warns.append(f"{tag}：「{x.get('name')}」不到 1 像素，会画成 1 像素并注明")
            summary.append(f"{tag}：最高 {mx/per:,.0f} 像素")
        elif t == "dots":
            per = float(s.get("per") or 0); groups = s.get("groups") or []
            if per <= 0 or not groups: errs.append(f"{tag}：需要 per > 0 和 groups"); continue
            counts = [max(1, round(float(g.get("value") or 0) / per)) if float(g.get("value") or 0) > 0 else 0 for g in groups]
            total = sum(counts)
            if total > MAX_DOTS: errs.append(f"{tag}：{total:,} 个点，超过 {MAX_DOTS:,}，把 per 调大")
            for g, c in zip(groups, counts):
                if float(g.get("value") or 0) / per < 0.5: warns.append(f"{tag}：「{g.get('label')}」不到 1 个点，会按 1 个点画并注明")
            summary.append(f"{tag}：共 {total:,} 个点（" + "、".join(f"{g.get('label')} {c:,}" for g, c in zip(groups, counts)) + "）")
        elif t == "ticker":
            if not float(s.get("rate") or 0) > 0: errs.append(f"{tag}：需要 rate > 0（每秒多少）")
            else: summary.append(f"{tag}：每秒 {float(s['rate']):,.2f}")
        else:
            errs.append(f"场景 {i}：不认识的类型 {t!r}，可用 ruler / stack / dots / ticker")
    return errs, warns, summary


def build(a):
    cfg = json.load(open(a.config, encoding="utf-8"))
    errs, warns, summary = validate(cfg)
    for s in summary: print("· " + s)
    for w in warns: print("! " + w)
    if errs:
        for e in errs: print("✗ " + e, file=sys.stderr)
        sys.exit(1)
    tpl = open(TEMPLATE, encoding="utf-8").read()
    data = json.dumps(cfg, ensure_ascii=False, indent=2).replace("</", "<\\/")
    block = "/* === TOSCALE CONFIG START === */\nwindow.TOSCALE = " + data + ";\n/* === TOSCALE CONFIG END === */"
    tpl = re.sub(r"/\* === TOSCALE CONFIG START === \*/.*?/\* === TOSCALE CONFIG END === \*/", lambda m: block, tpl, flags=re.S)
    title = html.escape(cfg.get("title", "比例尺"))
    tpl = tpl.replace("<title>比例尺</title>", f"<title>{title}</title>", 1)
    if a.fragment:
        body = tpl.split("<!-- TS:FRAGMENT-START -->", 1)[1].split("<!-- TS:FRAGMENT-END -->", 1)[0]
        body = body.replace("<!-- TS:FRAGMENT-BODY -->\n", "")
        tpl = f'<title>{title}</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n' + body.lstrip("\n")
    out = a.output or os.path.splitext(a.config)[0] + ".html"
    open(out, "w", encoding="utf-8").write(tpl)
    print(f"✓ 已生成 {out}")


def main():
    p = argparse.ArgumentParser(description="比例尺 to-scale 助手")
    sp = p.add_subparsers(dest="cmd", required=True)
    s1 = sp.add_parser("suggest", help="给出可选的比例")
    s1.add_argument("--length", type=float, help="标尺总长，或堆叠里最高那一项")
    s1.add_argument("--bodies", type=float, nargs="*", help="起点/终点物体的尺寸（与 length 同单位）")
    s1.add_argument("--count", type=float, help="点阵：所有分组加起来的总量")
    s1.add_argument("--unit", default="")
    s2 = sp.add_parser("build", help="校验配置并生成页面")
    s2.add_argument("config")
    s2.add_argument("-o", "--output")
    s2.add_argument("--fragment", action="store_true", help="输出 Claude Artifact 用的片段（不含 doctype/head/body）")
    a = p.parse_args()
    if a.cmd == "suggest":
        if not a.length and not a.count: p.error("suggest 需要 --length 或 --count")
        suggest(a)
    else:
        build(a)


if __name__ == "__main__":
    main()
