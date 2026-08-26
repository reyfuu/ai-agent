import re, sys, pathlib, collections
root = pathlib.Path('.')
fails, checks = [], 0
def chk(name, cond, detail=""):
    global checks; checks += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -> {detail}" if not cond and detail else ""))
    if not cond: fails.append(name)

docs = ["BRD.md","FRD.md","TRD.md","PRD.md","AGENTS.md","USERFLOW.md","DATAMODEL.md","API_CONTRACT.md"]
for d in docs: chk(f"dokumen {d} ada", (root/d).exists())
chk("README.md ada", (root/'README.md').exists())
_rd=(root/'README.md').read_text() if (root/'README.md').exists() else ""
chk("README memuat disclaimer", 'bukan nasihat investasi' in _rd)
for _l in re.findall(r"\]\(([\w./-]+\.md)\)", _rd):
    chk(f"tautan README -> {_l}", (root/_l).exists())

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


# 11. dokumen baru: USERFLOW / DATAMODEL / API_CONTRACT
uf=(root/'USERFLOW.md').read_text(); dm=(root/'DATAMODEL.md').read_text(); ac=(root/'API_CONTRACT.md').read_text()
for _d,_secs in {'USERFLOW.md':['Peta Navigasi','Alur Utama','Alur per Peran','Status yang Dilihat Pengguna'],
                 'DATAMODEL.md':['ERD','Tabel','Invariant Data','Indeks','Migrasi'],
                 'API_CONTRACT.md':['Konvensi','Bentuk Error Seragam','Endpoint','Matriks Otorisasi','Rate Limit']}.items():
    _t=(root/_d).read_text()
    for _s in _secs: chk(f"{_d} memuat bagian '{_s}'", _s in _t)

# matriks otorisasi API_CONTRACT harus sepadan dengan PRD
_mac=_matrix(ac,'## 4. Matriks Otorisasi')
chk("API_CONTRACT punya matriks otorisasi >=7 baris", len(_mac)>=7, len(_mac))

# setiap endpoint di API_CONTRACT ada di server.py
srv=(root/'server.py').read_text()
_eps=set(re.findall(r"`(?:GET|POST|PUT|DELETE|\*) (/api/[\w/{}.*-]+)`", ac))
_missing=[e for e in _eps if e.split('?')[0].split('{')[0].rstrip('/*').split('/')[2] not in srv]
chk("endpoint kontrak terimplementasi di server.py", not _missing, _missing)

# tabel DATAMODEL harus ada di skema db.py
dbsrc=(root/'agent'/'db.py').read_text()
for _tbl in ['tickers','prices','fundamentals','analyses','agent_runs','users','watchlist','audit_logs','settings']:
    chk(f"tabel {_tbl} ada di agent/db.py", f"CREATE TABLE {_tbl}" in dbsrc)

# ADR & batas arsitektur
adr=root/'docs'/'adr'/'001-orkestrasi-multi-agent.md'
chk("ADR-001 ada", adr.exists())
if adr.exists():
    _a=adr.read_text()
    for _s in ['Konteks','Keputusan','Konsekuensi','Alternatif yang ditolak','Kepatuhan']:
        chk(f"ADR-001 memuat '{_s}'", _s in _a)
    for _fw in ['LangChain','LangGraph','CrewAI']:
        chk(f"ADR-001 membahas {_fw}", _fw in _a)

# inti perhitungan bebas framework (dicek ulang di sini, bukan hanya di unittest)
ana=(root/'agent'/'analysis.py').read_text()
for _fw in ['langchain','langgraph','crewai']:
    chk(f"agent/analysis.py bebas {_fw}", f"import {_fw}" not in ana and f"from {_fw}" not in ana)
chk("run_analysis_fallback ada", 'def run_analysis_fallback' in (root/'agent'/'graph.py').read_text())

# requirements dipin dan dijustifikasi
req=(root/'requirements.txt').read_text()
chk("requirements.txt menunjuk ADR", 'adr' in req.lower())
chk("semua dependency dipin ke versi tepat", all('==' in l for l in req.splitlines() if l.strip() and not l.startswith('#')))

# UI: aksesibilitas & keamanan dasar
html=(root/'web'/'index.html').read_text(); js=(root/'web'/'app.js').read_text()
chk("HTML lang=id", 'lang="id"' in html)
chk("HTML punya main", '<main' in html)
chk("disclaimer di UI", 'bukan nasihat investasi' in html)
chk("tanpa CDN pihak ketiga", not re.search(r'src="https?://', html))
_kode=re.sub(r'/\*.*?\*/','',js,flags=re.S)
chk("app.js tidak memakai innerHTML", 'innerHTML' not in _kode)
chk("app.js tidak menghitung indikator", not re.search(r'\b(rsi|macd|bollinger)\s*\(', _kode, re.I))
chk("CSS punya @media print", '@media print' in (root/'web'/'style.css').read_text())

print(f"\n{checks - len(fails)}/{checks} lulus")
sys.exit(1 if fails else 0)
