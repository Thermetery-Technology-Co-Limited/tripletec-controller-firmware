import sqlite3, sys, base64, gzip, json, os, collections
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
src, outdir = sys.argv[1], sys.argv[2]
os.makedirs(outdir, exist_ok=True)
con = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True)
ph = [n for (n,) in con.execute("select name from sqlite_master where name like 'project_history_%'")][0]
hist = {u: (k, p, n, c) for u, k, p, n, c in con.execute(f'select uuid, key, parent, num, created_at from "{ph}"')}
ok = bad_tag = bad_gz = 0
heads = collections.Counter()
with open(os.path.join(outdir, "records.jsonl"), "w") as out:
    for rid, ru, hu, d, created in con.execute("select id, uuid, history_uuid, dataStr, created_at from history_data order by id"):
        key, parent, num, hcreated = hist[hu]
        raw = base64.b64decode(d)
        try:
            pt = AESGCM(bytes.fromhex(key)).decrypt(bytes.fromhex(hu), raw, None)
        except Exception:
            bad_tag += 1; continue
        try:
            txt = gzip.decompress(pt).decode("utf-8")
        except Exception:
            bad_gz += 1; continue
        ok += 1
        heads[txt[:40]] += 1
        out.write(json.dumps({"id": rid, "uuid": ru, "history": hu, "parent": parent, "created": created, "len": len(txt), "data": txt}, ensure_ascii=False) + "\n")
print(f"decrypted+gunzipped: {ok}, tag failures: {bad_tag}, gzip failures: {bad_gz}")
print("most common starts:")
for h, n in heads.most_common(12): print(f"  {n:4d}  {h!r}")
