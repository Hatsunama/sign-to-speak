$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$PowerShellExe = if ($PSVersionTable.PSEdition -eq 'Core') {
    Join-Path $PSHOME 'pwsh.exe'
} else {
    Join-Path $PSHOME 'powershell.exe'
}
$Installer = Join-Path $env:TEMP ("install-sign-to-speak-" + [Guid]::NewGuid().ToString('N') + '.ps1')

try {
    $headers = @{
        'User-Agent' = 'SignToSpeak-bootstrap'
    }
    $token = $env:GH_TOKEN
    if (-not $token) { $token = $env:GITHUB_TOKEN }
    if (-not $token) {
        try { $token = (gh auth token 2>$null) } catch { }
    }
    if ($token) { $headers['Authorization'] = "Bearer $token" }

    Invoke-WebRequest -UseBasicParsing `
        -Uri 'https://raw.githubusercontent.com/Hatsunama/sign-to-speak/main/scripts/install-sign-to-speak.ps1' `
        -Headers $headers `
        -OutFile $Installer

    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $Installer
    if ($LASTEXITCODE -ne 0) {
        throw "Sign To Speak installer failed (exit $LASTEXITCODE). The temporary download is removed. The previous install, if any, is left in place."
    }
}
finally {
    if (Test-Path -LiteralPath $Installer) {
        try {
            Remove-Item -LiteralPath $Installer -Force -ErrorAction Stop
            Write-Host 'Temporary installer script removed.'
        }
        catch {
            Write-Warning "Could not remove temporary installer ${Installer}: $($_.Exception.Message)"
        }
    }
}
