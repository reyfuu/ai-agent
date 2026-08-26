/* UI vanilla. Tidak ada logika finansial di sini: semua angka datang jadi
   dari server. Data dari server dirender dengan textContent, tidak pernah
   innerHTML, karena nama emiten dan narasi LLM adalah input tak dipercaya. */

'use strict';

const $ = (sel) => document.querySelector(sel);
const rupiah = (n) => new Intl.NumberFormat('id-ID').format(n);

let emitenAktif = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = new Error(data?.error?.message || 'Terjadi kesalahan.');
    e.code = data?.error?.code;
    throw e;
  }
  return data;
}

function kosongkan(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function sel(tag, teks, kelas) {
  const el = document.createElement(tag);
  if (teks !== undefined && teks !== null) el.textContent = String(teks);
  if (kelas) el.className = kelas;
  return el;
}

// ---------------------------------------------------------------- sesi

async function muatSesi() {
  try {
    const me = await api('/api/me');
    $('#siapa').textContent = `Masuk sebagai ${me.username} (${me.role})`;
    $('#panel-login').hidden = true;
    $('#panel-app').hidden = false;
    await muatWatchlist();
  } catch {
    $('#siapa').textContent = 'Belum masuk';
    $('#panel-login').hidden = false;
    $('#panel-app').hidden = true;
  }
}

$('#form-login').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('#galat-login').textContent = '';
  try {
    await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username: $('#username').value, password: $('#password').value }),
    });
    await muatSesi();
  } catch (e) {
    $('#galat-login').textContent = e.message;
  }
});

// ---------------------------------------------------------------- cari

$('#form-cari').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const daftar = $('#hasil-cari');
  kosongkan(daftar);
  const { items } = await api('/api/tickers?q=' + encodeURIComponent($('#q').value));
  if (!items.length) {
    daftar.appendChild(sel('li', 'Tidak ada emiten yang cocok.'));
    return;
  }
  for (const t of items) {
    const li = sel('li');
    li.appendChild(sel('span', `${t.code} — ${t.name}`));
    const btn = sel('button', 'Tambah ke watchlist', 'sekunder');
    btn.addEventListener('click', async () => {
      await api('/api/watchlist', { method: 'POST', body: JSON.stringify({ code: t.code }) });
      await muatWatchlist();
    });
    li.appendChild(btn);
    daftar.appendChild(li);
  }
});

// ---------------------------------------------------------------- watchlist

async function muatWatchlist() {
  const { items } = await api('/api/watchlist');
  const tbody = $('#tabel-watchlist tbody');
  kosongkan(tbody);
  $('#watchlist-kosong').hidden = items.length > 0;
  $('#tabel-watchlist').hidden = items.length === 0;

  for (const it of items) {
    const tr = sel('tr');
    tr.appendChild(sel('th', it.code)).scope = 'row';
    tr.appendChild(sel('td', it.name));
    const td = sel('td');

    const analisis = sel('button', 'Analisis');
    analisis.addEventListener('click', () => jalankanAnalisis(it.code, analisis));
    td.appendChild(analisis);

    const hapus = sel('button', 'Hapus', 'sekunder');
    hapus.addEventListener('click', async () => {
      await api('/api/watchlist/' + it.code, { method: 'DELETE' });
      await muatWatchlist();
    });
    td.appendChild(hapus);

    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

// ---------------------------------------------------------------- analisis

async function jalankanAnalisis(code, tombol) {
  emitenAktif = code;
  tombol.disabled = true;
  const status = $('#status-analisis');
  $('#panel-hasil').hidden = false;
  kosongkan(status);
  status.appendChild(sel('p', `Menganalisis ${code}…`));

  try {
    const hasil = await api('/api/analyze/' + code, { method: 'POST', body: '{}' });
    tampilkanHasil(hasil);
    try {
      const harga = await api('/api/prices/' + code);
      gambarGrafik(harga.items);
      isiTabelGrafik(harga.items);
    } catch {
      /* grafik opsional: kegagalannya tidak boleh menghapus skor */
    }
    if (hasil.id) muatRuns(hasil.id);
  } catch (e) {
    kosongkan(status);
    status.appendChild(sel('p', e.message, 'galat'));
  } finally {
    tombol.disabled = false;
  }
}

function tampilkanHasil(h) {
  const status = $('#status-analisis');
  kosongkan(status);

  const judul = sel('p');
  judul.appendChild(sel('strong', h.code));
  judul.appendChild(document.createTextNode(` · data per ${h.trade_date} `));
  if (h.stale) judul.appendChild(sel('span', '⚠ Data basi', 'badge badge-stale'));
  status.appendChild(judul);

  const skor = $('#ringkas-skor');
  kosongkan(skor);
  $('#narasi').textContent = '';
  kosongkan($('#tabel-indikator tbody'));

  if (h.status === 'insufficient_data') {
    const p = sel('p');
    p.appendChild(sel('span', 'Data belum cukup', 'badge badge-kurang'));
    p.appendChild(document.createTextNode(
      ` Dibutuhkan ${h.detail.required_days} hari bursa, tersedia ${h.detail.available_days}.`));
    status.appendChild(p);
    $('#disclaimer').textContent = h.disclaimer;
    return;
  }

  for (const [label, nilai] of [
    ['Skor teknikal', h.scores.tech],
    ['Skor fundamental', h.scores.funda ?? 'tidak tersedia'],
    ['Skor komposit', h.scores.total],
    ['Label', h.label],
    ['Keyakinan', h.confidence],
  ]) {
    const div = sel('div');
    div.appendChild(sel('dt', label));
    div.appendChild(sel('dd', nilai));
    skor.appendChild(div);
  }

  const tbody = $('#tabel-indikator tbody');
  const ind = h.data_snapshot.indicators || {};
  for (const [nama, nilai] of Object.entries(ind)) {
    if (nilai === null || nilai === undefined) continue;
    const tr = sel('tr');
    tr.appendChild(sel('th', nama)).scope = 'row';
    const td = sel('td', typeof nilai === 'number' ? nilai.toFixed(2) : nilai, 'angka');
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  $('#narasi').textContent = h.narrative || 'Narasi tidak tersedia saat ini.';
  if (h.narrative_status !== 'ok') {
    $('#narasi').appendChild(sel('em', ` (${h.narrative_status})`));
  }
  $('#disclaimer').textContent = h.disclaimer;
}

async function muatRuns(id) {
  const tbody = $('#tabel-runs tbody');
  kosongkan(tbody);
  try {
    const { items } = await api(`/api/analyses/id/${id}/runs`);
    for (const r of items) {
      const tr = sel('tr');
      tr.appendChild(sel('td', r.framework));
      tr.appendChild(sel('td', r.node));
      tr.appendChild(sel('td', r.status));
      tr.appendChild(sel('td', `${r.duration_ms} ms`, 'angka'));
      tbody.appendChild(tr);
    }
  } catch {
    /* jejak orkestrasi bersifat tambahan */
  }
}

// ---------------------------------------------------------------- grafik

function gambarGrafik(bars) {
  const c = $('#grafik');
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, c.width, c.height);
  if (!bars || bars.length < 2) return;

  const data = bars.slice(-120).map((b) => b.close);
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || 1;
  const pad = 24;
  const x = (i) => pad + (i / (data.length - 1)) * (c.width - pad * 2);
  const y = (v) => c.height - pad - ((v - min) / span) * (c.height - pad * 2);

  ctx.strokeStyle = '#d4dde6';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, c.height - pad);
  ctx.lineTo(c.width - pad, c.height - pad);
  ctx.stroke();

  ctx.strokeStyle = '#1c4f82';
  ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.stroke();

  ctx.fillStyle = '#5a6b7d';
  ctx.font = '12px system-ui';
  ctx.fillText(rupiah(max), 4, y(max) + 4);
  ctx.fillText(rupiah(min), 4, y(min) + 4);
  c.setAttribute('aria-label',
    `Grafik harga penutupan ${data.length} hari terakhir, terendah ${min}, tertinggi ${max} rupiah.`);
}

function isiTabelGrafik(bars) {
  const tbody = $('#tabel-grafik tbody');
  kosongkan(tbody);
  for (const b of bars.slice(-30)) {
    const tr = sel('tr');
    tr.appendChild(sel('th', b.trade_date)).scope = 'row';
    tr.appendChild(sel('td', rupiah(b.close), 'angka'));
    tbody.appendChild(tr);
  }
}

$('#btn-cetak').addEventListener('click', () => window.print());

muatSesi();
