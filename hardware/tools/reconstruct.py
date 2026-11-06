import json, sys, os, re, sqlite3, collections
dbpath, recpath, outdir = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)
recs = [json.loads(l) for l in open(recpath)]
recs.sort(key=lambda r: r["id"])
docs = {}       # uuid -> {"head": dict, "objs": {(type,id): (ticket, payload_str)}}
partial = collections.Counter()
for r in recs:
    cur = None
    for line in r["data"].split("\n"):
        if "||" not in line: continue
        h, p = line.split("||", 1)
        hd = json.loads(h)
        p = p[:-1] if p.endswith("|") else p
        t = hd["type"]
        if t == "EDIT_HEAD": continue
        if t == "DOCHEAD":
            pl = json.loads(p); cur = pl["uuid"]
            d = docs.setdefault(cur, {"head": None, "objs": {}})
            if d["head"] is None or float(pl["version"]) >= float(d["head"]["version"]): d["head"] = pl
            continue
        key = (t, hd.get("id", t))
        tk = hd.get("ticket", 0)
        objs = docs[cur]["objs"]
        old = objs.get(key)
        if old and tk < old[0]: continue
        if p.strip() == "":
            objs[key] = (tk, None)
        else:
            if old and old[1]:
                ok_, nk = set(json.loads(old[1]).keys()) if old[1].startswith("{") else set(), set(json.loads(p).keys()) if p.startswith("{") else set()
                if ok_ and nk and nk < ok_: partial[t] += 1
            objs[key] = (tk, p)
con = sqlite3.connect(f"file:{dbpath}?mode=ro&immutable=1", uri=True)
st = json.loads(con.execute("select structure from project_structures order by id desc limit 1").fetchone()[0])
expect = {}
for kind in ("sheets", "pcbs"):
    for u, v in st[kind].items(): expect[u] = (kind, v.get("title"), v["version"])
print("payloads that dropped keys vs previous (would indicate partial patches):", dict(partial) or "none")
for u, d in docs.items():
    live = {k: v for k, v in d["objs"].items() if v[1] is not None}
    c = collections.Counter(k[0] for k in live)
    title = expect.get(u, ("", "", ""))[1]
    meta = live.get(("META", "META"))
    if not title and meta: title = json.loads(meta[1]).get("title", "")
    exp = expect.get(u)
    match = ("MATCH" if exp and exp[2] == d["head"]["version"] else f"expected {exp[2]}") if exp else "not in project index"
    print(f"{d['head']['docType']:10s} {u} {title!r:28s} objs={len(live):6d} version={d['head']['version']} [{match}]")
    print("     ", ", ".join(f"{k}={n}" for k, n in c.most_common(14)))
    fn = os.path.join(outdir, f"{d['head']['docType']}_{re.sub(r'[^A-Za-z0-9_-]','_',title or u)}_{u}.txt")
    with open(fn, "w") as f:
        f.write(json.dumps({"type": "DOCHEAD"}) + "||" + json.dumps(d["head"], ensure_ascii=False) + "|\n")
        for (t, i), (tk, p) in sorted(live.items(), key=lambda kv: kv[1][0]):
            f.write(json.dumps({"type": t, "ticket": tk, "id": i}, ensure_ascii=False) + "||" + p + "|\n")
