import json, glob, os, math, csv, sys, collections

MIL2MM = 0.0254


def load(fn):
    out = []
    for line in open(fn, encoding="utf-8"):
        h, p = line.rstrip("\n").split("||", 1)
        p = p[:-1] if p.endswith("|") else p
        out.append((json.loads(h), json.loads(p) if p else None))
    return out


def by_uuid(docdir, prefix):
    m = {}
    for fn in glob.glob(os.path.join(docdir, f"{prefix}_*.txt")):
        m[fn.rsplit("_", 1)[1][:-4]] = fn
    return m


def fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


class Unhandled(collections.Counter):
    pass


unhandled = Unhandled()


def ring_to_svg(ring, tf=lambda x, y: (x, -y)):
    if not ring:
        return ""
    if ring[0] == "R":
        x, y, w, h, rot, rad = (ring[1:7] + [0, 0])[:6]
        rot = rot or 0
        rad = min(rad or 0, w / 2, h / 2)
        corners = [(x, y), (x + w, y), (x + w, y - h), (x, y - h)]
        def r(px, py):
            if not rot:
                return px, py
            a = math.radians(rot)
            dx, dy = px - x, py - y
            return x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a)
        if rad <= 0:
            pts = [tf(*r(*c)) for c in corners]
            return "M" + " L".join(f"{fmt(a)},{fmt(b)}" for a, b in pts) + " Z"
        segs = []
        pts = [(x + rad, y), (x + w - rad, y), (x + w, y - rad), (x + w, y - h + rad),
               (x + w - rad, y - h), (x + rad, y - h), (x, y - h + rad), (x, y - rad)]
        tp = [tf(*r(*p)) for p in pts]
        segs.append(f"M{fmt(tp[0][0])},{fmt(tp[0][1])}")
        for i in range(0, 8, 2):
            a, b = tp[i + 1], tp[(i + 2) % 8]
            segs.append(f"L{fmt(a[0])},{fmt(a[1])} A{fmt(rad)},{fmt(rad)} 0 0 1 {fmt(b[0])},{fmt(b[1])}")
        return " ".join(segs) + " Z"
    if ring[0] == "CIRCLE":
        cx, cy, r = ring[1:4]
        X, Y = tf(cx, cy)
        return f"M{fmt(X - r)},{fmt(Y)} a{fmt(r)},{fmt(r)} 0 1 0 {fmt(2 * r)},0 a{fmt(r)},{fmt(r)} 0 1 0 {fmt(-2 * r)},0 Z"
    i = 0
    cx, cy = ring[0], ring[1]
    X, Y = tf(cx, cy)
    out = [f"M{fmt(X)},{fmt(Y)}"]
    i = 2
    cmd = "L"
    while i < len(ring):
        tok = ring[i]
        if isinstance(tok, str):
            cmd = tok
            i += 1
            continue
        if cmd == "L":
            cx, cy = ring[i], ring[i + 1]
            X, Y = tf(cx, cy)
            out.append(f"L{fmt(X)},{fmt(Y)}")
            i += 2
        elif cmd in ("ARC", "CARC"):
            ang, ex, ey = ring[i], ring[i + 1], ring[i + 2]
            chord = math.hypot(ex - cx, ey - cy)
            s = abs(math.sin(math.radians(ang) / 2))
            r = chord / (2 * s) if s > 1e-9 else 0
            EX, EY = tf(ex, ey)
            if r == 0:
                out.append(f"L{fmt(EX)},{fmt(EY)}")
            else:
                large = 1 if abs(ang) > 180 else 0
                sweep = 0 if ang > 0 else 1
                out.append(f"A{fmt(r)},{fmt(r)} 0 {large} {sweep} {fmt(EX)},{fmt(EY)}")
            cx, cy = ex, ey
            i += 3
        else:
            unhandled["path:" + cmd] += 1
            i += 1
    return " ".join(out) + " Z"


def rings_of(path):
    if not path:
        return []
    if isinstance(path[0], list):
        return path
    return [path]


def pad_shape(pad):
    dp = pad.get("defaultPad") or {}
    t = dp.get("padType")
    w, h = dp.get("width", 0), dp.get("height", 0)
    if t == "RECT":
        rad = (dp.get("radius") or 0) / 100 * min(w, h) / 2 if dp.get("radius") else 0
        return ("rect", w, h, rad)
    if t in ("ELLIPSE", "OVAL"):
        return ("rect", w, h, min(w, h) / 2)
    if t == "POLYGON":
        return ("poly", dp.get("path"))
    unhandled["pad:" + str(t)] += 1
    return ("rect", w, h, 0)


def footprint_pads(fn):
    pads = []
    for h, p in load(fn):
        if h["type"] == "PAD" and p:
            pads.append(p)
    return pads


def footprint_graphics(fn):
    return [(h["type"], p) for h, p in load(fn) if h["type"] in ("POLY", "FILL") and p]


def transform_ring(ring, c):
    bottom = c.get("layerId") == 2
    a = math.radians(c.get("angle") or 0)
    def T(x, y):
        if bottom:
            x = -x
        return c["x"] + x * math.cos(a) - y * math.sin(a), c["y"] + x * math.sin(a) + y * math.cos(a)
    if not ring:
        return ring
    if ring[0] == "R":
        x, y, w, h = ring[1:5]
        ring = [x, y, "L", x + w, y, x + w, y - h, x, y - h, x, y]
    if ring[0] == "CIRCLE":
        cx, cy = T(ring[1], ring[2])
        return ["CIRCLE", cx, cy, ring[3]]
    out = list(T(ring[0], ring[1]))
    i, cmd = 2, "L"
    while i < len(ring):
        t = ring[i]
        if isinstance(t, str):
            cmd = t; out.append(t); i += 1; continue
        if cmd == "L":
            out += list(T(ring[i], ring[i + 1])); i += 2
        elif cmd in ("ARC", "CARC"):
            out.append(-ring[i] if bottom else ring[i]); out += list(T(ring[i + 1], ring[i + 2])); i += 3
        else:
            i += 1
    return out


def component_table(docdir):
    pcbfn = glob.glob(os.path.join(docdir, "PCB_*.txt"))[0]
    pcb = load(pcbfn)
    fps = by_uuid(docdir, "FOOTPRINT")
    devs = by_uuid(docdir, "DEVICE")
    comps = {h["id"]: p for h, p in pcb if h["type"] == "COMPONENT" and p}
    attrs = collections.defaultdict(dict)
    attr_objs = collections.defaultdict(dict)
    for h, p in pcb:
        if h["type"] == "ATTR" and p and p.get("parentId") in comps:
            attrs[p["parentId"]][p["key"]] = p["value"]
            attr_objs[p["parentId"]][p["key"]] = p
    rows = []
    for cid, c in comps.items():
        a = attrs[cid]
        fpname = ""
        if a.get("Footprint") in fps:
            fpname = os.path.basename(fps[a["Footprint"]])[len("FOOTPRINT_"):].rsplit("_", 1)[0]
        dmeta = {}
        if a.get("Device") in devs:
            for h, p in load(devs[a["Device"]]):
                if h["type"] == "META" and p:
                    dmeta = p
        da = dmeta.get("attributes", {})
        rows.append({
            "id": cid,
            "Designator": a.get("Designator", ""),
            "Side": "Top" if c.get("layerId") == 1 else "Bottom",
            "X_mm": round(c["x"] * MIL2MM, 4),
            "Y_mm": round(c["y"] * MIL2MM, 4),
            "Rotation": c.get("angle", 0) or 0,
            "Footprint": fpname,
            "Device": dmeta.get("title", ""),
            "Value": da.get("Value", "") or da.get("Resistance", "") or da.get("Capacitance", "") or da.get("Inductance", ""),
            "Manufacturer": da.get("Manufacturer", ""),
            "Manufacturer Part": da.get("Manufacturer Part", ""),
            "Supplier": da.get("Supplier", ""),
            "Supplier Part": da.get("Supplier Part", ""),
            "Datasheet": da.get("Datasheet", ""),
            "In BOM": da.get("Add into BOM", ""),
            "_comp": c,
            "_fp": fps.get(a.get("Footprint")),
            "_attr_objs": attr_objs[cid],
        })
    return pcb, rows


def write_csvs(rows, outdir, tag):
    os.makedirs(outdir, exist_ok=True)
    def dkey(r):
        d = r["Designator"]
        pre = "".join(ch for ch in d if not ch.isdigit())
        num = "".join(ch for ch in d if ch.isdigit())
        return (pre, int(num) if num else 0)
    rows = sorted(rows, key=dkey)
    with open(os.path.join(outdir, f"{tag}_placement.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Side", "X (mm)", "Y (mm)", "Rotation", "Footprint", "Device", "Manufacturer Part"])
        for r in rows:
            w.writerow([r["Designator"], r["Side"], r["X_mm"], r["Y_mm"], r["Rotation"], r["Footprint"], r["Device"], r["Manufacturer Part"]])
    groups = collections.OrderedDict()
    for r in rows:
        if str(r["In BOM"]).lower() == "no":
            continue
        k = (r["Device"], r["Footprint"], r["Manufacturer Part"], r["Supplier Part"])
        groups.setdefault(k, []).append(r)
    with open(os.path.join(outdir, f"{tag}_BOM.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Qty", "Designators", "Device", "Value", "Footprint", "Manufacturer", "Manufacturer Part", "Supplier", "Supplier Part", "Datasheet"])
        for k, rs in groups.items():
            r = rs[0]
            w.writerow([len(rs), " ".join(x["Designator"] for x in rs), r["Device"], r["Value"], r["Footprint"],
                        r["Manufacturer"], r["Manufacturer Part"], r["Supplier"], r["Supplier Part"], r["Datasheet"]])
    return len(rows), len(groups)


def render_svg(docdir, outfn, title):
    pcb, rows = component_table(docdir)
    objs = collections.defaultdict(list)
    pours = {}
    for h, p in pcb:
        if p is None:
            continue
        objs[h["type"]].append((h, p))
        if h["type"] == "POUR":
            pours[h["id"]] = p
    layers = collections.defaultdict(list)  # layer -> list of svg elements
    COPPER = {1: "#ff2a2a", 2: "#3355ff"}
    for h, p in objs["POURED"]:
        pid = json.loads(h["id"])[1]
        pour = pours.get(pid)
        if not pour:
            continue
        lid = pour["layerId"]
        for pf in p.get("pourFill", []):
            d = " ".join(ring_to_svg(r, tf=lambda x, y: (x * 10, -y * 10)) for r in rings_of(pf.get("path")))
            if pf.get("fill", True):
                layers[lid].append(f'<path d="{d}" fill-rule="evenodd"/>')
            else:
                layers[lid].append(f'<path d="{d}" fill="none" stroke-width="{fmt(pf.get("strokeWidth") or 1)}"/>')
    for t in ("FILL",):
        for h, p in objs[t]:
            d = " ".join(ring_to_svg(r) for r in rings_of(p.get("path")))
            layers[p["layerId"]].append(f'<path d="{d}" fill-rule="evenodd"/>')
    for h, p in objs["TEARDROP"]:
        layers[p["layerId"]].append(f'<path d="{ring_to_svg(p["path"])}"/>')
    for h, p in objs["LINE"]:
        layers[p["layerId"]].append(
            f'<line x1="{fmt(p["startX"])}" y1="{fmt(-p["startY"])}" x2="{fmt(p["endX"])}" y2="{fmt(-p["endY"])}" stroke-width="{fmt(p["width"])}"/>')
    for h, p in objs["ARC"]:
        if "startX" in p:
            ring = [p["startX"], p["startY"], "ARC", p.get("arcAngle", 0), p["endX"], p["endY"]]
            d = ring_to_svg(ring)[:-2]
            layers[p["layerId"]].append(f'<path d="{d}" fill="none" stroke-width="{fmt(p.get("width", 1))}"/>')
        else:
            unhandled["ARC-shape"] += 1
    for h, p in objs["POLY"]:
        d = ring_to_svg(p["path"]) if p["path"] and not isinstance(p["path"][0], list) else " ".join(ring_to_svg(r) for r in p["path"])
        layers[p["layerId"]].append(f'<path d="{d}" fill="none" stroke-width="{fmt(p.get("width", 1))}"/>')
    for h, p in objs["CIRCLE"]:
        if "centerX" in p:
            layers[p["layerId"]].append(f'<circle cx="{fmt(p["centerX"])}" cy="{fmt(-p["centerY"])}" r="{fmt(p["radius"])}" fill="none" stroke-width="{fmt(p.get("width", 1))}"/>')
    # component pads
    pad_count = 0
    for r in rows:
        c = r["_comp"]
        if not r["_fp"]:
            unhandled["component-without-footprint"] += 1
            continue
        bottom = c.get("layerId") == 2
        ang = math.radians(c.get("angle") or 0)
        for pad in footprint_pads(r["_fp"]):
            px, py = pad["centerX"], pad["centerY"]
            if bottom:
                px = -px
            X = c["x"] + px * math.cos(ang) - py * math.sin(ang)
            Y = c["y"] + px * math.sin(ang) + py * math.cos(ang)
            pa = (pad.get("padAngle") or 0)
            tot = (-pa if bottom else pa) + (c.get("angle") or 0)
            shape = pad_shape(pad)
            lid = pad.get("layerId")
            tl = {1: 2, 2: 1}.get(lid, lid) if bottom else lid
            targets = [1, 2] if tl == 12 else [tl]
            if shape[0] == "rect":
                _, w, hgt, rad = shape
                el = (f'<rect x="{fmt(-w / 2)}" y="{fmt(-hgt / 2)}" width="{fmt(w)}" height="{fmt(hgt)}" rx="{fmt(rad)}" '
                      f'transform="translate({fmt(X)},{fmt(-Y)}) rotate({fmt(-tot)})"/>')
            else:
                pts = shape[1] or []
                d = ring_to_svg(pts)
                sx = -1 if bottom else 1
                el = f'<path d="{d}" transform="translate({fmt(X)},{fmt(-Y)}) rotate({fmt(-(c.get("angle") or 0))}) scale({sx},1)"/>'
            for t in targets:
                layers[("pad", t)].append(el)
            hole = pad.get("hole")
            if hole:
                hw = hole.get("width", 0)
                hh = hole.get("height", hw)
                layers["holes"].append(
                    f'<rect x="{fmt(-hw / 2)}" y="{fmt(-hh / 2)}" width="{fmt(hw)}" height="{fmt(hh)}" rx="{fmt(min(hw, hh) / 2)}" '
                    f'transform="translate({fmt(X)},{fmt(-Y)}) rotate({fmt(-tot)})"/>')
            pad_count += 1
        for kind, g in footprint_graphics(r["_fp"]):
            lid = g.get("layerId")
            if lid not in (3, 4):
                continue
            tl = {3: 4, 4: 3}[lid] if bottom else lid
            path = g.get("path")
            if kind == "POLY":
                d = ring_to_svg(transform_ring(path, c))
                layers[tl].append(f'<path d="{d[:-2] if not (path and path[0] in ("R", "CIRCLE")) else d}" fill="none" stroke-width="{fmt(g.get("width", 1))}"/>')
            else:
                d = " ".join(ring_to_svg(transform_ring(rg, c)) for rg in rings_of(path))
                layers[("silkfill", tl)].append(f'<path d="{d}" fill-rule="evenodd"/>')
    free_pads = 0
    for h, p in objs["PAD"]:
        shape = pad_shape(p)
        X, Y = p["centerX"], p["centerY"]
        if shape[0] == "rect":
            _, w, hgt, rad = shape
            el = (f'<rect x="{fmt(-w / 2)}" y="{fmt(-hgt / 2)}" width="{fmt(w)}" height="{fmt(hgt)}" rx="{fmt(rad)}" '
                  f'transform="translate({fmt(X)},{fmt(-Y)}) rotate({fmt(-(p.get("padAngle") or 0))})"/>')
            layers["freepads"].append(el)
        hole = p.get("hole")
        if hole:
            hw, hh = hole.get("width", 0), hole.get("height", hole.get("width", 0))
            layers["holes"].append(f'<rect x="{fmt(-hw / 2)}" y="{fmt(-hh / 2)}" width="{fmt(hw)}" height="{fmt(hh)}" rx="{fmt(min(hw, hh) / 2)}" '
                                   f'transform="translate({fmt(X)},{fmt(-Y)})"/>')
        free_pads += 1
    for h, p in objs["VIA"]:
        layers["vias"].append(f'<circle cx="{fmt(p["centerX"])}" cy="{fmt(-p["centerY"])}" r="{fmt(p["viaDiameter"] / 2)}"/>')
        layers["holes"].append(f'<circle cx="{fmt(p["centerX"])}" cy="{fmt(-p["centerY"])}" r="{fmt(p["holeDiameter"] / 2)}"/>')
    texts = []
    for h, p in objs["STRING"]:
        col = "#ffcc00" if p["layerId"] == 3 else "#66cc33" if p["layerId"] == 4 else "#bbbbbb"
        texts.append(f'<text x="{fmt(p["x"])}" y="{fmt(-p["y"])}" font-size="{fmt(p["fontSize"])}" fill="{col}" '
                     f'transform="rotate({fmt(-(p.get("angle") or 0))} {fmt(p["x"])} {fmt(-p["y"])})">{escape(p["text"])}</text>')
    for r in rows:
        ao = r["_attr_objs"].get("Designator")
        if ao and ao.get("x") is not None and ao.get("valueVisible", True):
            col = "#ffcc00" if ao["layerId"] == 3 else "#66cc33"
            texts.append(f'<text x="{fmt(ao["x"])}" y="{fmt(-ao["y"])}" font-size="{fmt(ao["fontSize"])}" fill="{col}" '
                         f'transform="rotate({fmt(-(ao.get("angle") or 0))} {fmt(ao["x"])} {fmt(-ao["y"])})">{escape(ao["value"])}</text>')
    outline = [p for h, p in objs["POLY"] if p["layerId"] == 11]
    xs, ys = [], []
    for p in outline:
        path = p["path"]
        if path[0] == "R":
            x, y, w, hh = path[1:5]
            xs += [x, x + w]; ys += [y, y - hh]
        else:
            nums = [v for v in path if not isinstance(v, str)]
            xs += nums[0::2]; ys += nums[1::2]
    if not xs:
        for h, p in objs["LINE"]:
            xs += [p["startX"], p["endX"]]; ys += [p["startY"], p["endY"]]
    board_w, board_h = max(xs) - min(xs), max(ys) - min(ys)
    vx = xs + [p["x"] for h, p in objs["STRING"]]
    vy = ys + [p["y"] for h, p in objs["STRING"]] + [p["y"] + p["fontSize"] for h, p in objs["STRING"]]
    pad_ = 450
    minx, maxx, miny, maxy = min(vx) - pad_, max(vx) + pad_ + 400, min(vy) - pad_, max(vy) + pad_
    W, H = maxx - minx, maxy - miny

    def group(key, fill, stroke, opacity=1.0, extra=""):
        els = layers.get(key, [])
        if not els:
            return ""
        return f'<g fill="{fill}" stroke="{stroke}" stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}" {extra}>' + "\n".join(els) + "</g>"
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{fmt(minx)} {fmt(-maxy)} {fmt(W)} {fmt(H)}" width="{fmt(W * 0.5)}" height="{fmt(H * 0.5)}">',
           f'<rect x="{fmt(minx)}" y="{fmt(-maxy)}" width="{fmt(W)}" height="{fmt(H)}" fill="#000"/>',
           group(2, COPPER[2], COPPER[2], 0.85), group(("pad", 2), COPPER[2], "none", 0.85),
           group(1, COPPER[1], COPPER[1], 0.75), group(("pad", 1), COPPER[1], "none", 0.9),
           group("freepads", "#a0a0a0", "none"),
           group("vias", "#a0a0a0", "none"), group("holes", "#202020", "none"),
           group(3, "none", "#ffcc00"), group(4, "none", "#66cc33"),
           group(("silkfill", 3), "#ffcc00", "none"), group(("silkfill", 4), "#66cc33", "none"),
           group(11, "none", "#ff00ff"), group(12, "#909090", "#909090", 0.8),
           '<g font-family="sans-serif">' + "\n".join(texts) + "</g>",
           "</svg>"]
    open(outfn, "w", encoding="utf-8").write("\n".join(svg))
    return {"tracks": len(objs["LINE"]), "vias": len(objs["VIA"]), "pads": pad_count, "board_pads": free_pads,
            "live_pours": sum(1 for h, p in objs["POURED"] if json.loads(h["id"])[1] in pours),
            "components": len(rows), "bottom_side_parts": sum(1 for r in rows if r["Side"] == "Bottom"),
            "size_mm": (round(board_w * MIL2MM, 2), round(board_h * MIL2MM, 2))}


def escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    docdir, outdir, tag = sys.argv[1:4]
    os.makedirs(outdir, exist_ok=True)
    pcb, rows = component_table(docdir)
    n, g = write_csvs(rows, outdir, tag)
    print(f"{tag}: {n} placed parts, {g} BOM lines")
    stats = render_svg(docdir, os.path.join(outdir, f"{tag}_pcb.svg"), tag)
    print(stats)
    print("unhandled:", dict(unhandled) or "none")
