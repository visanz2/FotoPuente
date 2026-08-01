# FotoPuente - abre el puerto en el Firewall de Windows.
# Lo lanza "Permitir en el Firewall.bat" ya con permisos de administrador.

$ErrorActionPreference = 'Stop'

function Titulo($t) {
    Write-Host ""
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host "  $('-' * $t.Length)" -ForegroundColor DarkGray
}

# --- Puerto: se lee de config.json para no descuadrarse si lo cambiaste ---
$puerto = 8765
$config = Join-Path $PSScriptRoot 'config.json'
if (Test-Path $config) {
    try {
        $c = Get-Content $config -Raw | ConvertFrom-Json
        if ($c.puerto) { $puerto = [int]$c.puerto }
    } catch {
        Write-Host "  (no se pudo leer config.json, se usa el puerto 8765)" -ForegroundColor DarkYellow
    }
}

Titulo "FotoPuente - permiso de red (puerto $puerto)"

# --- Perfil de las redes conectadas ---------------------------------------
# Una regla para el perfil "Privado" NO se aplica si Windows tiene tu WiFi
# clasificada como "Publica", que es lo habitual al conectarse por primera vez.
$perfiles = Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne 'Disconnected' }

Write-Host ""
Write-Host "  Redes conectadas ahora mismo:"
foreach ($p in $perfiles) {
    $color = 'Green'
    if ($p.NetworkCategory -eq 'Public') { $color = 'Yellow' }
    Write-Host ("    {0,-28} {1}" -f $p.Name, $p.NetworkCategory) -ForegroundColor $color
}

$publicas = @($perfiles | Where-Object { $_.NetworkCategory -eq 'Public' })
$alcance = 'Private,Domain'

if ($publicas.Count -gt 0) {
    Titulo "Atencion"
    Write-Host "  Tu red esta marcada como PUBLICA. Con esa clasificacion Windows"
    Write-Host "  bloquea las conexiones entrantes aunque crees la regla."
    Write-Host ""
    Write-Host "  Si es tu WiFi de casa, lo correcto es marcarla como PRIVADA."
    Write-Host ""
    Write-Host "    [1] Marcarla como privada y crear la regla  (recomendado en casa)"
    Write-Host "    [2] Dejarla publica y crear la regla tambien para redes publicas"
    Write-Host "    [3] Cancelar"
    Write-Host ""

    do {
        $opcion = Read-Host "  Elige 1, 2 o 3"
    } while ($opcion -notin @('1', '2', '3'))

    if ($opcion -eq '3') {
        Write-Host ""
        Write-Host "  Cancelado. No se ha cambiado nada." -ForegroundColor DarkGray
        exit 0
    }

    if ($opcion -eq '1') {
        foreach ($p in $publicas) {
            try {
                Set-NetConnectionProfile -Name $p.Name -NetworkCategory Private
                Write-Host "  '$($p.Name)' ahora es una red privada." -ForegroundColor Green
            } catch {
                Write-Host "  No se pudo cambiar '$($p.Name)': $_" -ForegroundColor Red
            }
        }
    } else {
        $alcance = 'Private,Domain,Public'
        Write-Host ""
        Write-Host "  Aviso: la regla valdra tambien en redes publicas (bares, hoteles...)." -ForegroundColor Yellow
        Write-Host "  Para transferir solo en casa, ejecuta 'Quitar del Firewall.bat' al terminar."
    }
}

# --- Crear la regla --------------------------------------------------------
Titulo "Creando la regla"

try {
    Get-NetFirewallRule -DisplayName 'FotoPuente' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    New-NetFirewallRule `
        -DisplayName 'FotoPuente' `
        -Description 'Permite que el iPhone envie fotos a FotoPuente por WiFi' `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $puerto `
        -Profile ([string]$alcance) `
        -Enabled True | Out-Null

    Write-Host ""
    Write-Host "  Listo. Puerto $puerto abierto para: $alcance" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Ya puedes arrancar FotoPuente y escanear el QR con el iPhone."
} catch {
    Write-Host ""
    Write-Host "  No se pudo crear la regla:" -ForegroundColor Red
    Write-Host "  $_"
    Write-Host ""
    Write-Host "  Alternativa manual: Firewall de Windows Defender > Reglas de entrada"
    Write-Host "  > Nueva regla > Puerto > TCP > $puerto > Permitir la conexion."
}

Write-Host ""
