/* FotoPuente - logica de la pagina del iPhone. */
'use strict';

const PARALELAS = 3;      // subidas simultaneas
const REINTENTOS = 3;     // intentos por archivo antes de darlo por fallido
const LOTE_PLAN = 800;    // archivos por peticion al comprobar duplicados

const $ = (id) => document.getElementById(id);

const el = {
  destino: $('destino'), senal: $('senal'),
  pasoElegir: $('paso-elegir'), pasoResumen: $('paso-resumen'),
  pasoEnvio: $('paso-envio'), pasoFinal: $('paso-final'),
  labelElegir: $('label-elegir'), avisoSeleccion: $('aviso-seleccion'),
  notaFormato: $('nota-formato'),
  entrada: $('entrada'), analizando: $('analizando'), resumen: $('resumen'),
  nNuevos: $('n-nuevos'), nRepes: $('n-repes'), nPeso: $('n-peso'),
  btnEnviar: $('btn-enviar'), btnOtra: $('btn-otra'), avisoNada: $('aviso-nada'),
  progresoTxt: $('progreso-txt'), progresoPct: $('progreso-pct'), barra: $('barra'),
  mVelocidad: $('m-velocidad'), mRestante: $('m-restante'), mEnviado: $('m-enviado'),
  btnPausa: $('btn-pausa'), btnCancelar: $('btn-cancelar'), registro: $('registro'),
  finalIcono: $('final-icono'), finalTitulo: $('final-titulo'),
  finalDetalle: $('final-detalle'), btnMas: $('btn-mas'),
  btnReintentar: $('btn-reintentar'),
};

let cola = [];          // File[] pendientes de enviar
let fallidos = [];      // File[] que agotaron los reintentos
let pausado = false;
let cancelado = false;
let enCurso = new Set();

const cuenta = { total: 0, hechos: 0, guardados: 0, duplicados: 0, errores: 0 };
const bytes = { total: 0, completados: 0, vuelo: new Map() };
let muestras = [];      // [tiempo, bytesEnviados] para calcular la velocidad

/* ---------- utilidades ---------- */

function humano(n) {
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
}

function duracion(seg) {
  if (!isFinite(seg) || seg < 0) return '—';
  if (seg < 60) return Math.ceil(seg) + ' s';
  const m = Math.floor(seg / 60);
  if (m < 60) return m + ' min ' + String(Math.floor(seg % 60)).padStart(2, '0') + ' s';
  return Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min';
}

function mostrar(seccion) {
  for (const s of [el.pasoElegir, el.pasoResumen, el.pasoEnvio, el.pasoFinal]) {
    s.classList.toggle('oculto', s !== seccion);
  }
}

/* ---------- estado de la conexion ---------- */

async function comprobarConexion() {
  try {
    const r = await fetch('/api/ping', { cache: 'no-store' });
    if (!r.ok) throw new Error();
    const d = await r.json();
    el.destino.textContent = 'Guardando en: ' + d.destino;
    el.senal.className = 'senal viva';
    aplicarFormato(d.formato);
  } catch {
    el.destino.textContent = 'Sin conexión con el PC';
    el.senal.className = 'senal muerta';
  }
}

/* El formato lo decide el panel del PC. En modo "original" quitamos el
   filtro accept para que iOS entregue el HEIC tal cual, sin convertirlo a
   JPEG; en "compatible" pedimos imagenes y iOS las convierte a JPEG. */
function aplicarFormato(formato) {
  if (formato === 'original') {
    el.entrada.removeAttribute('accept');
    el.notaFormato.textContent =
      'Modo originales: se envían tal cual (HEIC/MOV). Tu PC ya sabe abrirlos.';
  } else {
    el.entrada.setAttribute('accept', 'image/*,video/*');
    el.notaFormato.textContent =
      'Modo compatible: las fotos llegan como JPEG, que se abre en cualquier PC.';
  }
}

/* ---------- paso 1: analisis ---------- */

/* iOS abre el selector al pulsar la etiqueta. Anotamos ese momento para
   distinguir "iOS no pudo cargar tantas" (vuelve con lista vacia enseguida)
   de una cancelacion normal del usuario. */
let momentoApertura = 0;
el.labelElegir.addEventListener('click', () => {
  momentoApertura = Date.now();
  el.avisoSeleccion.classList.add('oculto');
});

el.entrada.addEventListener('change', async () => {
  const archivos = Array.from(el.entrada.files || []);
  el.entrada.value = '';                 // permite reelegir los mismos

  if (!archivos.length) {
    // Safari en iOS devuelve la lista vacia cuando se queda sin memoria al
    // preparar demasiadas fotos o videos a la vez. Tras varios segundos con
    // el selector abierto, un resultado vacio casi siempre es eso, no un
    // "cancelar".
    if (Date.now() - momentoApertura > 2500) {
      el.avisoSeleccion.classList.remove('oculto');
    }
    return;
  }
  el.avisoSeleccion.classList.add('oculto');

  mostrar(el.pasoResumen);
  el.analizando.classList.remove('oculto');
  el.resumen.classList.add('oculto');

  let nuevos = [];
  let repetidos = 0;
  let descartados = 0;
  try {
    for (let i = 0; i < archivos.length; i += LOTE_PLAN) {
      const lote = archivos.slice(i, i + LOTE_PLAN);
      const r = await fetch('/api/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          archivos: lote.map((f) => ({ nombre: f.name, tamano: f.size })),
        }),
      });
      if (!r.ok) throw new Error('El PC rechazó la comprobación');
      const d = await r.json();
      repetidos += d.repetidos || 0;
      descartados += d.descartados || 0;
      for (const idx of d.nuevos) nuevos.push(archivos[i + idx]);
    }
  } catch (e) {
    // Sin comprobacion previa seguimos igual: el servidor descarta duplicados.
    nuevos = archivos;
    repetidos = descartados = 0;
  }

  cola = nuevos;
  fallidos = [];
  const peso = nuevos.reduce((a, f) => a + f.size, 0);

  el.nNuevos.textContent = nuevos.length;
  el.nRepes.textContent = repetidos;
  el.nPeso.textContent = humano(peso);
  el.btnEnviar.classList.toggle('oculto', nuevos.length === 0);
  el.avisoNada.classList.toggle('oculto', nuevos.length > 0);
  if (descartados) {
    el.avisoNada.classList.remove('oculto');
    el.avisoNada.textContent = (nuevos.length ? '' : 'Nada nuevo que enviar. ') +
      descartados + (descartados === 1
        ? ' archivo no es una foto ni un vídeo y se omitirá.'
        : ' archivos no son fotos ni vídeos y se omitirán.');
  } else {
    el.avisoNada.textContent = 'Nada nuevo que enviar: ya está todo en el PC.';
  }

  el.analizando.classList.add('oculto');
  el.resumen.classList.remove('oculto');
});

el.btnOtra.addEventListener('click', () => { mostrar(el.pasoElegir); });
el.btnMas.addEventListener('click', () => { mostrar(el.pasoElegir); });

el.btnReintentar.addEventListener('click', () => {
  cola = fallidos.slice();
  fallidos = [];
  arrancarEnvio();
});

el.btnEnviar.addEventListener('click', arrancarEnvio);

/* ---------- mantener la pantalla encendida ---------- */
/* Solo funciona en contexto seguro (HTTPS), por eso el servidor cifra. */

let bloqueoPantalla = null;

async function mantenerDespierto() {
  if (!('wakeLock' in navigator) || bloqueoPantalla) return;
  try {
    bloqueoPantalla = await navigator.wakeLock.request('screen');
    bloqueoPantalla.addEventListener('release', () => { bloqueoPantalla = null; });
  } catch { /* la puede denegar si hay poca bateria */ }
}

function soltarPantalla() {
  if (bloqueoPantalla) {
    bloqueoPantalla.release().catch(() => {});
    bloqueoPantalla = null;
  }
}

/* iOS lo suelta al cambiar de app; se recupera al volver. */
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && enCurso.size) mantenerDespierto();
});

/* ---------- paso 2: envio ---------- */

function arrancarEnvio() {
  if (!cola.length) return;

  cuenta.total = cola.length;
  cuenta.hechos = cuenta.guardados = cuenta.duplicados = cuenta.errores = 0;
  bytes.total = cola.reduce((a, f) => a + f.size, 0);
  bytes.completados = 0;
  bytes.vuelo.clear();
  muestras = [[performance.now(), 0]];
  pausado = cancelado = false;
  enCurso.clear();
  el.registro.innerHTML = '';
  el.btnPausa.textContent = 'Pausar';

  mostrar(el.pasoEnvio);
  refrescarProgreso();
  mantenerDespierto();

  const pendientes = cola.slice();
  cola = [];
  const trabajadores = [];
  for (let i = 0; i < Math.min(PARALELAS, pendientes.length); i++) {
    trabajadores.push(trabajador(pendientes));
  }
  Promise.all(trabajadores).then(terminar);
}

async function trabajador(pendientes) {
  while (!cancelado) {
    while (pausado && !cancelado) await new Promise((r) => setTimeout(r, 300));
    const archivo = pendientes.shift();
    if (!archivo) return;

    let ok = false;
    for (let intento = 1; intento <= REINTENTOS && !cancelado; intento++) {
      try {
        const res = await subir(archivo);
        anotar(archivo.name, res.estado === 'duplicado' ? 'dup' : 'ok');
        if (res.estado === 'duplicado') cuenta.duplicados++; else cuenta.guardados++;
        ok = true;
        break;
      } catch (e) {
        if (cancelado) return;
        if (e && e.definitivo) break;           // no tiene sentido reintentar
        if (intento < REINTENTOS) {
          await new Promise((r) => setTimeout(r, 600 * intento));
        }
      }
    }
    if (!ok && !cancelado) {
      cuenta.errores++;
      fallidos.push(archivo);
      anotar(archivo.name, 'error');
    }
    cuenta.hechos++;
    bytes.vuelo.delete(archivo);
    bytes.completados += archivo.size;
    refrescarProgreso();
  }
}

function subir(archivo) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    enCurso.add(xhr);
    xhr.open('PUT', '/api/subir', true);
    xhr.setRequestHeader('X-FP-Nombre', encodeURIComponent(archivo.name));
    xhr.setRequestHeader('X-FP-Fecha', String(archivo.lastModified || 0));
    xhr.timeout = 15 * 60 * 1000;

    xhr.upload.onprogress = (ev) => {
      bytes.vuelo.set(archivo, ev.loaded);
      refrescarProgreso();
    };
    xhr.onload = () => {
      enCurso.delete(xhr);
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch { /* respuesta no JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(d);
      // 4xx = el archivo no vale; reintentarlo daria el mismo resultado.
      const err = new Error(d.motivo || ('HTTP ' + xhr.status));
      err.definitivo = xhr.status >= 400 && xhr.status < 500;
      reject(err);
    };
    xhr.onerror = () => { enCurso.delete(xhr); reject(new Error('red')); };
    xhr.ontimeout = () => { enCurso.delete(xhr); reject(new Error('tiempo agotado')); };
    xhr.onabort = () => { enCurso.delete(xhr); reject(new Error('cancelado')); };

    xhr.send(archivo);
  });
}

/* ---------- progreso ---------- */

let ultimoPintado = 0;

function refrescarProgreso(forzar) {
  const ahora = performance.now();
  if (!forzar && ahora - ultimoPintado < 120) return;
  ultimoPintado = ahora;

  let vuelo = 0;
  for (const v of bytes.vuelo.values()) vuelo += v;
  const enviados = bytes.completados + vuelo;
  const pct = bytes.total ? Math.min(100, (enviados / bytes.total) * 100) : 0;

  el.barra.style.width = pct.toFixed(1) + '%';
  el.progresoPct.textContent = pct.toFixed(0) + ' %';
  el.progresoTxt.textContent = cuenta.hechos + ' / ' + cuenta.total;
  el.mEnviado.textContent = humano(enviados);

  // Velocidad media de los ultimos 8 segundos.
  muestras.push([ahora, enviados]);
  while (muestras.length > 2 && ahora - muestras[0][0] > 8000) muestras.shift();
  const [t0, b0] = muestras[0];
  const dt = (ahora - t0) / 1000;
  if (dt > 0.8) {
    const vel = (enviados - b0) / dt;
    el.mVelocidad.textContent = humano(vel) + '/s';
    el.mRestante.textContent = vel > 1024
      ? 'faltan ' + duracion((bytes.total - enviados) / vel)
      : '—';
  }
}

function anotar(nombre, clase) {
  const textos = { ok: 'guardada', dup: 'ya estaba', error: 'falló' };
  const li = document.createElement('li');
  const n = document.createElement('span');
  n.className = 'nombre';
  n.textContent = nombre;
  const et = document.createElement('span');
  et.className = 'etiqueta ' + clase;
  et.textContent = textos[clase];
  li.append(n, et);
  el.registro.prepend(li);
  while (el.registro.children.length > 60) el.registro.lastChild.remove();
}

/* ---------- controles ---------- */

el.btnPausa.addEventListener('click', () => {
  pausado = !pausado;
  el.btnPausa.textContent = pausado ? 'Reanudar' : 'Pausar';
});

el.btnCancelar.addEventListener('click', () => {
  if (!confirm('¿Cancelar la transferencia?')) return;
  cancelado = true;
  for (const xhr of enCurso) xhr.abort();
  enCurso.clear();
});

function terminar() {
  soltarPantalla();
  refrescarProgreso(true);
  mostrar(el.pasoFinal);

  const parcial = cuenta.errores > 0 || cancelado;
  el.finalIcono.textContent = parcial ? '!' : '✓';
  el.finalIcono.className = 'final-icono' + (parcial ? ' parcial' : '');
  el.finalTitulo.textContent = cancelado ? 'Transferencia cancelada'
    : parcial ? 'Terminado con incidencias' : 'Todo listo';

  const partes = [];
  if (cuenta.guardados) {
    partes.push(cuenta.guardados + (cuenta.guardados === 1 ? ' guardada' : ' guardadas'));
  }
  if (cuenta.duplicados) {
    partes.push(cuenta.duplicados + (cuenta.duplicados === 1 ? ' ya estaba' : ' ya estaban'));
  }
  if (cuenta.errores) {
    partes.push(cuenta.errores + (cuenta.errores === 1 ? ' con error' : ' con errores'));
  }
  el.finalDetalle.textContent = partes.join(' · ') +
    (cuenta.guardados ? ' (' + humano(bytes.completados) + ')' : '');

  el.btnReintentar.classList.toggle('oculto', fallidos.length === 0);
}

/* ---------- arranque ---------- */

comprobarConexion();
setInterval(comprobarConexion, 10000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) comprobarConexion();
});
