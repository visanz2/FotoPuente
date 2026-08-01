# FotoPuente - elimina la regla de firewall creada por firewall.ps1.

Write-Host ""
$regla = Get-NetFirewallRule -DisplayName 'FotoPuente' -ErrorAction SilentlyContinue

if ($regla) {
    try {
        $regla | Remove-NetFirewallRule
        Write-Host "  Regla eliminada." -ForegroundColor Green
        Write-Host "  FotoPuente ya no acepta conexiones desde la red."
    } catch {
        Write-Host "  No se pudo eliminar la regla: $_" -ForegroundColor Red
    }
} else {
    Write-Host "  No habia ninguna regla de FotoPuente que quitar." -ForegroundColor DarkGray
}

Write-Host ""
