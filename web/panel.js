/* FotoPuente - panel de control (solo accesible desde este PC). */
'use strict';

const $ = (id) => document.getElementById(id);

const el = {
  qr: $('qr'), ip: $('ip'), campoIp: $('campo-ip'), url: $('url'),
  btnCopiar: $('btn-copiar'), version: $('version'), notaTls: $('nota-tls'),
  carpeta: $('carpeta'), organizar: $('organizar'),
  btnExaminar: $('btn-examinar'), btnGuardar: $('btn-guardar'),
  btnAbrir: $('btn-abrir'), avisoGuardado: $('aviso-guardado'),
  cRecibidos: $('c-recibidos'), cBytes: $('c-bytes'), cDup: $('c-dup'),
  cErr: $('c-err'), cHistorico: $('c-historico'), cLibre: $('c-libre'),
  registro: $('registro'), btnOlvidar: $('btn-olvidar'),
  avisoFormato: $('aviso-formato'),
};

let estado = null;

function humano(n) {
  if (n === null || n === undefined) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
}

/* ---------- carga del estado ---------- */

async function cargar() {
  const r = await fetch('/api/estado', { cache: 'no-store' });
  estado = await r.json();

  el.version.textContent = 'v' + estado.version;
  el.carpeta.value = estado.destino;
  el.organizar.value = estado.organizar;

  // No pisar la selección mientras el usuario la está tocando.
  const radio = document.querySelector(
    `input[name=formato][value="${estado.formato}"]`);
  if (radio && !document.activeElement.matches('input[name=formato]')) {
    radio.checked = true;
  }

  if (el.ip.options.length !== estado.ips.length) {
    el.ip.innerHTML = '';
    for (const ip of estado.ips) {
      const o = document.createElement('option');
      o.value = o.textContent = ip;
      el.ip.append(o);
    }
    el.campoIp.classList.toggle('oculto', estado.ips.length < 2);
  }
  refrescarQr();
  refrescarCifras();

  if (estado.ultimos && estado.ultimos.length && !el.registro.dataset.lleno) {
    el.registro.innerHTML = '';
    for (const e of estado.ultimos.slice().reverse()) anotar(e, true);
  }
}

function refrescarQr() {
  const ip = el.ip.value || (estado && estado.ips[0]);
  if (!ip) return;
  el.qr.src = '/qr.svg?ip=' + encodeURIComponent(ip) + '&t=' + Date.now();
  // El esquema lo decide el servidor: el panel se sirve en claro, pero el
  // movil entra por HTTPS. Escribir "http://" aqui rompería el enlace.
  el.url.textContent = estado.esquema + '://' + ip + ':' + estado.puerto +
    '/?k=' + estado.url.split('k=')[1];
  el.notaTls.classList.toggle('oculto', estado.esquema !== 'https');
}

function refrescarCifras() {
  el.cRecibidos.textContent = estado.recibidos;
  el.cBytes.textContent = humano(estado.bytes);
  el.cDup.textContent = estado.duplicados;
  el.cErr.textContent = estado.errores;
  el.cHistorico.textContent = estado.historico_archivos + ' archivos (' +
    humano(estado.historico_bytes) + ')';
  el.cLibre.textContent = humano(estado.libre);
}

el.ip.addEventListener('change', refrescarQr);

el.btnCopiar.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(el.url.textContent);
    el.btnCopiar.textContent = 'Copiado';
    setTimeout(() => { el.btnCopiar.textContent = 'Copiar'; }, 1500);
  } catch { /* el portapapeles puede estar bloqueado */ }
});

/* ---------- ajustes ---------- */

function avisar(texto, clase) {
  el.avisoGuardado.textContent = texto;
  el.avisoGuardado.className = 'aviso-guardado ' + clase;
  setTimeout(() => el.avisoGuardado.classList.add('oculto'), 4000);
}

el.btnExaminar.addEventListener('click', async () => {
  el.btnExaminar.disabled = true;
  el.btnExaminar.textContent = 'Elige…';
  try {
    const r = await fetch('/api/explorar', { method: 'POST' });
    const d = await r.json();
    if (d.ok) el.carpeta.value = d.carpeta;
    else if (d.error) avisar('No se pudo abrir el selector: ' + d.error, 'error');
  } catch {
    avisar('No se pudo abrir el selector de carpetas.', 'error');
  }
  el.btnExaminar.disabled = false;
  el.btnExaminar.textContent = 'Examinar…';
});

el.btnGuardar.addEventListener('click', async () => {
  const r = await fetch('/api/carpeta', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      carpeta: el.carpeta.value,
      organizar: el.organizar.value,
    }),
  });
  const d = await r.json();
  if (d.ok) {
    avisar('Guardado. Las próximas fotos irán a ' + d.carpeta, 'ok');
    cargar();
  } else {
    avisar(d.error || 'No se pudo guardar.', 'error');
  }
});

el.btnAbrir.addEventListener('click', () => {
  fetch('/api/abrir', { method: 'POST' });
});

el.btnOlvidar.addEventListener('click', async () => {
  if (!confirm(
    'Se olvidará qué fotos se transfirieron ya.\n\n' +
    'Los archivos NO se borran, pero la próxima vez el iPhone volverá a ' +
    'enviarlas todas.\n\n¿Continuar?')) return;
  await fetch('/api/olvidar', { method: 'POST' });
  cargar();
});

/* El formato se guarda en cuanto se elige, sin botón aparte. */
for (const radio of document.querySelectorAll('input[name=formato]')) {
  radio.addEventListener('change', async () => {
    if (!radio.checked) return;
    try {
      const r = await fetch('/api/formato', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ formato: radio.value }),
      });
      const d = await r.json();
      if (d.ok) {
        el.avisoFormato.textContent = radio.value === 'original'
          ? 'Guardado. Recuerda: si el iPhone ya tiene la página abierta, se '
            + 'aplica en unos segundos. Las próximas fotos irán en HEIC.'
          : 'Guardado. Las próximas fotos llegarán como JPEG.';
        el.avisoFormato.className = 'aviso-guardado ok';
      } else {
        el.avisoFormato.textContent = d.error || 'No se pudo guardar.';
        el.avisoFormato.className = 'aviso-guardado error';
      }
    } catch {
      el.avisoFormato.textContent = 'No se pudo guardar.';
      el.avisoFormato.className = 'aviso-guardado error';
    }
    setTimeout(() => el.avisoFormato.classList.add('oculto'), 5000);
  });
}

/* ---------- eventos en vivo ---------- */

function anotar(e, alFinal) {
  if (e.resultado === 'error' && !e.nombre) return;
  const vacio = el.registro.querySelector('.vacio');
  if (vacio) vacio.remove();
  el.registro.dataset.lleno = '1';

  const tr = document.createElement('tr');

  const td1 = document.createElement('td');
  td1.textContent = e.nombre || '?';
  td1.title = e.ruta || '';

  const td2 = document.createElement('td');
  td2.textContent = e.fecha || '—';
  if (e.origen === 'exif') {
    const s = document.createElement('span');
    s.className = 'fuente';
    s.textContent = 'EXIF';
    s.title = 'Fecha leída del propio archivo';
    td2.append(s);
  }

  const td3 = document.createElement('td');
  td3.textContent = e.tamano ? humano(e.tamano) : '—';

  const td4 = document.createElement('td');
  const et = document.createElement('span');
  const clase = e.resultado === 'guardado' ? 'ok'
    : e.resultado === 'duplicado' ? 'duplicado' : 'error';
  et.className = 'etiqueta ' + clase;
  et.textContent = e.resultado === 'guardado' ? 'guardada'
    : e.resultado === 'duplicado' ? 'ya estaba'
    : (e.motivo ? 'error: ' + e.motivo : 'error');
  td4.append(et);

  tr.append(td1, td2, td3, td4);
  if (alFinal) el.registro.append(tr); else el.registro.prepend(tr);
  while (el.registro.children.length > 200) el.registro.lastChild.remove();
}

/* Durante una transferencia grande llegan cientos de eventos por minuto;
   refrescar el estado completo en cada uno seria un desperdicio. */
let recargaPendiente = null;

function cargarConFreno() {
  if (recargaPendiente) return;
  recargaPendiente = setTimeout(() => {
    recargaPendiente = null;
    cargar();
  }, 1500);
}

function escuchar() {
  const es = new EventSource('/api/eventos');
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    if (d.tipo === 'archivo') {
      anotar(d, false);
      cargarConFreno();
    } else if (d.tipo === 'estado') {
      cargar();
    }
  };
  es.onerror = () => {
    es.close();
    setTimeout(escuchar, 3000);   // el servidor se habrá reiniciado
  };
}

cargar().then(escuchar);
setInterval(cargar, 30000);
