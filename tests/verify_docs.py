import re, sys, pathlib, collections
root = pathlib.Path('.')
fails, checks = [], 0
def chk(name, cond, detail=""):
    global checks; checks += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {detail}" if not cond and detail else ""))
    if not cond: fails.append(name)

docs = ["BRD.md","FRD.md","TRD.md","PRD.md","AGENTS.md"]
for d in docs: chk(f"dokumen {d} ada", (root/d).exists())

# 1. tidak ada sisa istilah POS di dokumen & agent
pos_terms = re.compile(r"\bayam goreng\b|\bkasir\b|\bstruk\b|\bomzet\b|\bshift\b|sale_items|stock_movements|node:sqlite|\bexpress\b|npm test|\bPOS\b", re.I)
targets = docs + [str(p) for p in root.glob('.claude/agents/*.md')]
for t in targets:
    hits = [l for l in (root/t).read_text().splitlines() if pos_terms.search(l)]
    chk(f"tanpa sisa istilah POS: {t}", not hits, hits[:2])

# 2. artefak Node hilang
for a in ["package.json","package-lock.json","node_modules"]:
    chk(f"artefak Node terhapus: {a}", not (root/a).exists())

# 3. subagent: nama unik, frontmatter valid, nama == nama file
names = collections.Counter()
for p in sorted(root.glob('.claude/agents/*.md')):
    txt = p.read_text()
    m = re.match(r"^---\nname: ([\w-]+)\ndescription: (.+)\ntools: (.+)\nmodel: (.+)\n---\n", txt)
    chk(f"frontmatter valid: {p.name}", bool(m))
    if m:
        names[m.group(1)] += 1
        chk(f"nama cocok file: {p.name}", m.group(1) == p.stem, m.group(1))
        chk(f"deskripsi punya batas JANGAN: {p.name}", "JANGAN" in m.group(2))
chk("nama subagent unik", all(v == 1 for v in names.values()), dict(names))
chk("ada 5 subagent saham", len(names) == 5, list(names))

# 4. referensi ID kebutuhan di agent benar-benar ada di FRD
frd = (root/'FRD.md').read_text()
frd_ids = set(re.findall(r"\bF-\d+\.\d+\b", frd))
chk("FRD punya >=25 kebutuhan ber-ID", len(frd_ids) >= 25, len(frd_ids))
for p in sorted(root.glob('.claude/agents/*.md')):
    refs = set(re.findall(r"\bF-\d+\.\d+\b", p.read_text()))
    missing = refs - frd_ids
    chk(f"referensi F-id valid: {p.name}", not missing, missing)

# 5. tautan markdown antar dokumen tidak menggantung
for d in docs:
    for link in re.findall(r"\]\(([A-Za-z0-9_.-]+\.md)[^)]*\)", (root/d).read_text()):
        chk(f"tautan {d} -> {link}", (root/link).exists())

# 6. BRD/FRD/TRD punya bagian wajib
must = {"BRD.md": ["Latar Belakang","Tujuan Bisnis","Ruang Lingkup","Risiko","Kriteria Penerimaan"],
        "FRD.md": ["Hak Akses","Kebutuhan Fungsional","Alur Utama","Validasi","Non-Fungsional","Kriteria Penerimaan"],
        "TRD.md": ["Arsitektur","Skema Data","Kontrak API","Keamanan","Pengujian","Deployment"]}
for d, secs in must.items():
    t = (root/d).read_text()
    for s in secs: chk(f"{d} memuat bagian '{s}'", s in t)

# 7. konsistensi peran di semua dokumen
for d in ["PRD.md","FRD.md","AGENTS.md"]:
    t = (root/d).read_text()
    tl = t.lower(); chk(f"peran guest/analyst/admin konsisten di {d}", all(r in tl for r in ["guest","analyst","admin"]))

# 8. skema SQL di TRD parseable & append-only tables ada
import sqlite3
sql = re.search(r"```sql\n(.*?)```", (root/'TRD.md').read_text(), re.S).group(1)
try:
    c = sqlite3.connect(':memory:'); c.executescript(sql)
    tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    ok = True
except Exception as e:
    ok = False; tables = set(); err = e
chk("skema SQL di TRD valid (dieksekusi sqlite3)", ok, locals().get('err'))
for t in ["tickers","prices","analyses","users","watchlist","audit_logs","settings","fundamentals"]:
    chk(f"tabel {t} terdefinisi", t in tables)
if ok:
    cols = {r[1]: r[2] for r in c.execute("pragma table_info(prices)")}
    for m in ["open","high","low","close","volume"]:
        chk(f"prices.{m} bertipe INTEGER (bukan float)", cols.get(m) == "INTEGER", cols.get(m))
    chk("analyses punya data_snapshot", "data_snapshot" in {r[1] for r in c.execute("pragma table_info(analyses)")})

# 9. .gitignore melindungi rahasia & db
gi = (root/'.gitignore').read_text()
for pat in ["stocks.db",".env","__pycache__"]:
    chk(f".gitignore memuat {pat}", pat in gi)


# 10. konsistensi silang PRD <-> FRD <-> TRD <-> AGENTS
prd=(root/'PRD.md').read_text(); frd=(root/'FRD.md').read_text()
trd=(root/'TRD.md').read_text(); ag=(root/'AGENTS.md').read_text()

def _matrix(txt, head):
    blok = txt[txt.index(head):][:2500]; out={}
    for ln in blok.splitlines():
        if ln.startswith('|') and ('\u2705' in ln or '\u274c' in ln):
            c=[x.strip() for x in ln.strip('|').split('|')]
            out[c[0].lower().replace('*','')]=tuple(c[1:4])
    return out
mp=_matrix(prd,'### 2.2'); mf=_matrix(frd,'## 2. Aktor')
chk("matriks hak akses PRD >=8 baris", len(mp)>=8, len(mp))
sama=[k for k in mp if k in mf]
chk("matriks PRD & FRD beririsan >=5 baris", len(sama)>=5, sama)
for k in sama:
    chk(f"hak akses konsisten PRD/FRD: '{k}'", mp[k]==mf[k], f"PRD{mp[k]} vs FRD{mf[k]}")

st=set(re.findall(r"`(ok|insufficient_data|stale|error)`", prd))
chk("PRD mendefinisikan 4 status analisis", st=={'ok','insufficient_data','stale','error'}, st)
for s in ['insufficient_data','stale']:
    chk(f"status '{s}' dipakai juga di FRD", s in frd)
    chk(f"status '{s}' dipakai juga di TRD/AGENTS", s in trd or s in ag)

chk("PRD punya 9 fitur P-x", len(re.findall(r"\| (P-\d) \|", prd))==9)
for _p,_f in {'P-1':'F-1.1','P-2':'F-1.2','P-3':'F-2.1','P-4':'F-2.6','P-5':'F-3.2',
              'P-6':'F-4.2','P-7':'F-5.3','P-8':'F-5.4','P-9':'F-6.1'}.items():
    chk(f"fitur {_p} tertutup kebutuhan FRD {_f}", _f in frd)

blok=re.search(r"## Struktur file\n\n```\n(.*?)```", ag, re.S).group(1)
mod=set(re.findall(r"`(agent/\w+\.py|server\.py)`", trd))
chk("semua modul TRD tercantum di struktur file AGENTS", not (mod-set(re.findall(r"^([\w./]+\.py)", blok, re.M))), mod-set(re.findall(r"^([\w./]+\.py)", blok, re.M)))

chk("TRD memakai ketiga peran", set(re.findall(r"\b(guest|analyst|admin)\b", trd))=={'guest','analyst','admin'})
for _l,_pat in [("60 hari bursa",r"60 hari"),("100 emiten",r"100 emiten"),("bobot total 100",r"total (?:tepat|wajib)? ?100")]:
    _ada=[d for d,tx in [('PRD',prd),('FRD',frd),('TRD',trd),('AGENTS',ag)] if re.search(_pat,tx)]
    chk(f"angka '{_l}' konsisten di >=2 dokumen", len(_ada)>=2, _ada)
_bad=[l for tx in (prd,frd,trd,ag) for l in tx.splitlines()
      if re.search(r"^\s*[-*]?\s*(rekomendasi )?(beli|jual) (sekarang|saham)", l, re.I)]
chk("tidak ada instruksi beli/jual direktif", not _bad, _bad[:2])

print(f"\n{checks - len(fails)}/{checks} lulus")
sys.exit(1 if fails else 0)
