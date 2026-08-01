# FotoPuente

Pasa las fotos y vídeos del iPhone a Windows por WiFi, sin cables, sin iTunes y
sin instalar nada en el móvil.

El PC levanta una pequeña web en tu red local. Escaneas un QR con la cámara del
iPhone, eliges las fotos en Safari y se guardan directamente en la carpeta que
tú digas — incluida una carpeta compartida de red.

---

## Puesta en marcha

1. Doble clic en **`Iniciar FotoPuente.bat`**.
   Se abre el panel de control en el navegador con un código QR.

2. La primera vez, doble clic en **`Permitir en el Firewall.bat`**
   (pedirá permisos de administrador). Sin esto Windows bloquea la conexión
   del iPhone.

   Si tu WiFi está marcada como *pública*, el script te avisará y te dejará
   pasarla a *privada*, que es lo correcto en casa. Con la red en modo público
   Windows bloquea las conexiones entrantes aunque exista la regla.

3. En el panel, elige **dónde quieres guardar las fotos** y pulsa
   *Guardar cambios*.

4. Abre la **Cámara** del iPhone, apunta al QR y toca el aviso que aparece.

5. La primera vez Safari dirá que **la conexión no es privada**. Es esperado:
   el certificado lo ha generado tu propio PC, no una autoridad externa.
   Toca *Mostrar detalles* → *visitar este sitio web*. Solo se pregunta una
   vez por dispositivo.

6. En el iPhone: *Elegir fotos y vídeos* → seleccionas → *Enviar al PC*.

El iPhone y el PC deben estar en la **misma red WiFi**.

### Por qué HTTPS

Safari en iOS ya no abre páginas por HTTP plano: intenta ascender la conexión
a HTTPS y, si no encuentra nada, deja la pestaña **en blanco o en negro** sin
mensaje de error. Por eso FotoPuente cifra por defecto, con un certificado que
se genera solo en el primer arranque y se guarda en `datos/certificado/`.

De paso, al ser una conexión segura el navegador permite **mantener la pantalla
encendida** durante la transferencia, así no se corta al apagarse el móvil.

Si el certificado te da problemas puedes volver al modo antiguo con
`py -3 fotopuente.py --http`, pero es probable que Safari no lo abra.

---

## Qué hace por ti

**No repite fotos.** Lleva un registro de todo lo transferido. Puedes
seleccionar el carrete entero cada vez: solo se envía lo nuevo. La comprobación
es doble — por nombre y tamaño antes de enviar, y por contenido (SHA-256) al
recibir, así que una misma foto renombrada tampoco se duplica.

**Ordena por la fecha real de la foto.** Lee la fecha de captura del propio
archivo (EXIF en fotos JPEG y HEIC, `mvhd` en los vídeos), no la fecha de la
transferencia. Puedes elegir cómo agrupar:

```
Fotos\2026\2026-07\IMG_4821.jpg
```

Además le pone esa fecha al archivo en Windows, para que el Explorador ordene
bien.

**Aguanta los cortes.** Cada archivo se reintenta hasta 3 veces. Si algo falla,
puedes reintentar solo los fallidos desde el propio móvil.

**No pisa nada.** Si ya existe un archivo con el mismo nombre pero distinto
contenido, guarda `IMG_0001 (2).jpg` en vez de sobrescribir.

**Solo tú puedes subir.** El enlace lleva un código de acceso. Otro dispositivo
de la misma WiFi que entre sin él no puede enviar nada.

**Tú eliges el formato: JPEG o HEIC.** En el panel, sección *Formato de las
fotos*:

- **Compatible (JPEG)**: el iPhone convierte cada foto a JPEG. Se abre en
  cualquier Windows y cualquier programa. Es lo recomendado si no estás seguro.
- **Originales (HEIC)**: el iPhone intenta enviar el HEIC tal cual, sin
  convertir. Ocupan la mitad, conservan la calidad original y entran más por
  tanda (menos memoria = menos probable el bloqueo de Safari). Tu Windows ya
  tiene los códecs para abrirlos.

  Aviso: no todas las versiones de iOS respetan el modo original; algunas
  convierten a JPEG igualmente. Se comprueba mirando la extensión en la columna
  *Archivo* del panel: si pone `.HEIC`, funcionó. Y si algún programa viejo de
  Windows no abre un `.heic`, vuelve a poner *Compatible* y reenvía esas
  (recuerda vaciar antes el histórico de duplicados si ya estaban).

---

## Guardar en una carpeta compartida

En el panel, escribe la ruta en *Dónde se guardan*. Vale cualquiera de estas:

| Tipo | Ejemplo |
|---|---|
| Carpeta local | `D:\Fotos\iPhone` |
| Carpeta compartida de otro PC | `\\MI-SERVIDOR\Fotos` |
| Unidad de red mapeada | `Z:\Fotos` |

Si es una carpeta compartida con contraseña, Windows debe tener ya guardadas
las credenciales (conéctate una vez desde el Explorador marcando *Recordar mis
credenciales*).

Para compartir una carpeta de este mismo PC: clic derecho sobre ella →
*Propiedades* → *Uso compartido* → *Compartir…*

---

## Opciones desde la consola

```bash
py -3 fotopuente.py --carpeta "\\MI-PC\Fotos" --puerto 8765 --organizar anio-mes
```

| Opción | Qué hace |
|---|---|
| `--carpeta` | Carpeta destino |
| `--puerto` | Puerto TCP (por defecto 8765) |
| `--organizar` | `plano`, `anio`, `anio-mes` o `anio-mes-dia` |
| `--sin-panel` | No abrir el panel al arrancar |
| `--http` | Servir sin cifrar (Safari moderno lo rechaza) |
| `--registro` | Mostrar en consola todas las peticiones recibidas |

`--registro` es lo primero que hay que probar si el móvil no conecta: si al
escanear el QR no aparece **ninguna línea** en la consola del PC, el problema
es de red o firewall y no llega ni la petición.

Los ajustes se guardan en `config.json` y el registro de lo transferido en
`datos/indice.db`.

---

## Si algo no va

**La página sale en blanco o en negro, sin nada.**
Es el síntoma de acceder por `http://` a un servidor que no lo acepta. Asegúrate
de que la dirección empieza por **`https://`**. Si escribiste la dirección a mano
en una versión anterior, Safari puede tenerla guardada: borra el historial de ese
sitio o vuelve a escanear el QR, que siempre lleva el esquema correcto.

**Safari dice que solo se permiten conexiones HTTPS.**
Lo mismo: usa el enlace `https://` del panel. Ya no hace falta desactivar ningún
ajuste de seguridad del iPhone.

**El iPhone no abre la página.**
Arranca con `py -3 fotopuente.py --registro` y mira la consola mientras escaneas.
Si no aparece ninguna línea, no llega la petición. Por orden de probabilidad:

1. No has ejecutado `Permitir en el Firewall.bat`.
2. Tu red está clasificada como *pública* en Windows. Vuelve a ejecutar ese
   script y elige la opción 1. Para comprobarlo:
   *Ajustes → Red e Internet → WiFi → tu red → Tipo de perfil de red*.
3. El iPhone está en datos móviles o en otra WiFi (la de un repetidor o la
   banda de invitados cuentan como otra red distinta).
4. FotoPuente no está arrancado en el PC.

**Quiero cerrar el puerto cuando no lo uso.**
`Quitar del Firewall.bat` elimina la regla. Vuelve a ejecutar
`Permitir en el Firewall.bat` cuando lo necesites otra vez.

**Hay varias IPs en el panel.**
Si el PC tiene WiFi y cable a la vez, prueba la otra dirección en el desplegable
*Dirección de red*: el QR se regenera solo.

**La transferencia se para al bloquear el iPhone.**
Safari se congela con la pantalla apagada. Para tandas grandes:
*Ajustes → Pantalla y brillo → Bloqueo automático → Nunca*. Si aun así se
corta, al desbloquear se reanuda, y lo ya enviado no se repite.

**Las fotos llegan como JPEG y no como HEIC.**
Lo hace iOS al subir por web. Si las quieres en HEIC original:
*Ajustes → Fotos → Transferir a Mac o PC → Mantener originales*.

**En el iPhone solo me deja enviar 2 o 3 fotos; si elijo muchas no pasa nada.**
No es un fallo de FotoPuente: es un límite de memoria de Safari. Al seleccionar
muchas fotos (sobre todo HEIC o vídeos), iOS tiene que prepararlas todas de
golpe y, si se pasa, devuelve la selección vacía sin avisar. **Solución: envía
en tandas de unas 40 fotos** (los vídeos, de pocos en pocos). Como FotoPuente
recuerda lo enviado, puedes ir mandando grupos sin repetir ninguna. Es una
limitación de iOS que afecta a cualquier web, no tiene arreglo del lado del PC.

**Quiero volver a enviarlo todo.**
En el panel, *Vaciar histórico de duplicados*. No borra ningún archivo, solo
olvida qué se había enviado.

---

## Cómo está hecho

Python 3.9+ y **solo la librería estándar**. No hay `pip install` ni
dependencias que se rompan con el tiempo.

| Archivo | Contenido |
|---|---|
| `fotopuente.py` | Servidor, índice de duplicados y API |
| `fechas.py` | Lectura de EXIF (JPEG) y `mvhd` (MP4/MOV) |
| `qrlite.py` | Generador de códigos QR |
| `certificado.py` | Certificado autofirmado para HTTPS |
| `web/` | Interfaz del móvil y panel del PC |

Única excepción a lo de las dependencias: para crear el certificado hace falta
el módulo `cryptography` **o** el ejecutable `openssl` (viene con Git para
Windows). Tu equipo tiene los dos. Si faltaran ambos, el programa te lo dice y
puedes arrancar con `--http`.

El panel y los ajustes solo responden a `localhost`: desde la red únicamente se
puede subir, y con el código de acceso.
