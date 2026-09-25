# Sign To Speak installer.
# Downloads one commit of Hatsunama/sign-to-speak from GitHub, installs its
# dependencies, and removes the temporary archive. Package id:
# com.xmilo_at_your_side.sign_to_speak

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$Repository = 'Hatsunama/sign-to-speak'
$PackageName = 'com.xmilo_at_your_side.sign_to_speak'
$TempRoot = $null
$OwnsTemp = $false
$Incoming = $null
$Destination = $null
$Previous = $null

function Test-ChildPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root
    )
    $Full = [IO.Path]::GetFullPath($Path)
    $RootFull = [IO.Path]::GetFullPath($Root)
    $Separator = [IO.Path]::DirectorySeparatorChar
    if (-not $RootFull.EndsWith($Separator)) {
        $RootFull += $Separator
    }
    return $Full.StartsWith($RootFull, [StringComparison]::OrdinalIgnoreCase)
}

function Get-GitHubHeaders {
    $headers = @{
        'User-Agent' = 'SignToSpeak-installer'
        'Accept' = 'application/vnd.github+json'
    }
    $token = $env:GH_TOKEN
    if (-not $token) { $token = $env:GITHUB_TOKEN }
    if (-not $token) {
        try { $token = (gh auth token 2>$null) } catch { }
    }
    if ($token) { $headers['Authorization'] = "Bearer $token" }
    $headers
}

function Resolve-NpmCommand {
    foreach ($Name in @('npm.cmd', 'npm')) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) { return $Command.Source }
    }
    throw 'npm was not found. Install Node.js 20 or newer and add it to PATH.'
}

try {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw 'node was not found. Install Node.js 20 or newer and add it to PATH.'
    }
    if (-not $env:LOCALAPPDATA) {
        throw 'LOCALAPPDATA is not set, so there is no install folder.'
    }

    $Npm = Resolve-NpmCommand
    $Headers = Get-GitHubHeaders
    $Commit = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/commits/main" -Headers $Headers
    $Sha = [string]$Commit.sha
    if ($Sha -notmatch '^[0-9a-f]{40}$') {
        throw 'GitHub did not return a commit SHA for main.'
    }

    $TempRoot = Join-Path $env:TEMP ("sign-to-speak-install-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $TempRoot | Out-Null
    $OwnsTemp = $true
    $Zip = Join-Path $TempRoot 'source.zip'
    $Extract = Join-Path $TempRoot 'extract'
    New-Item -ItemType Directory -Path $Extract | Out-Null

    Write-Host "Downloading $Repository @ $Sha"
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/$Repository/archive/$Sha.zip" `
        -Headers @{ 'User-Agent' = 'SignToSpeak-installer' } `
        -OutFile $Zip

    Expand-Archive -LiteralPath $Zip -DestinationPath $Extract
    Remove-Item -LiteralPath $Zip -Force

    $Sources = @(Get-ChildItem -LiteralPath $Extract -Directory)
    if ($Sources.Count -ne 1) {
        throw 'The GitHub archive did not contain exactly one project folder.'
    }
    $Source = $Sources[0]
    if ($Source.Name -cne "sign-to-speak-$Sha") {
        throw "Archive folder was $($Source.Name), expected sign-to-speak-$Sha."
    }

    Write-Host 'Installing dependencies.'
    & $Npm ci --prefix $Source.FullName --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed with exit code $LASTEXITCODE."
    }

    $AppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $Destination = Join-Path $AppDataRoot 'SignToSpeak'
    $Incoming = Join-Path $AppDataRoot ("SignToSpeak.incoming-" + [Guid]::NewGuid().ToString('N'))
    $Previous = Join-Path $AppDataRoot 'SignToSpeak.previous'

    if (Test-Path -LiteralPath $Previous) {
        $PreviousFull = [IO.Path]::GetFullPath($Previous)
        if (-not (Test-ChildPath -Path $PreviousFull -Root $AppDataRoot)) {
            throw "Refusing to remove unexpected path: $PreviousFull"
        }
        Remove-Item -LiteralPath $PreviousFull -Recurse -Force
    }

    Move-Item -LiteralPath $Source.FullName -Destination $Incoming
    if (Test-Path -LiteralPath $Destination) {
        Move-Item -LiteralPath $Destination -Destination $Previous
    }
    Move-Item -LiteralPath $Incoming -Destination $Destination
    Set-Content -LiteralPath (Join-Path $Destination '.install-commit') -Value $Sha -NoNewline

    if (Test-Path -LiteralPath $Previous) {
        $PreviousFull = [IO.Path]::GetFullPath($Previous)
        if ((Test-ChildPath -Path $PreviousFull -Root $AppDataRoot) -and
            ([IO.Path]::GetFileName($PreviousFull) -eq 'SignToSpeak.previous')) {
            try {
                Remove-Item -LiteralPath $PreviousFull -Recurse -Force -ErrorAction Stop
            }
            catch {
                Write-Warning "Could not remove the previous install at ${PreviousFull}: $($_.Exception.Message)"
            }
        }
    }

    Write-Host "Sign To Speak installed at $Destination"
    Write-Host "Package id: $PackageName"
    Write-Host "Commit: $Sha"
    Write-Host "Start it with: npm run dev --prefix `"$Destination`""
    exit 0
}
finally {
    if ($OwnsTemp -and $TempRoot) {
        try {
            $TempFull = [IO.Path]::GetFullPath($TempRoot)
            if (-not (Test-ChildPath -Path $TempFull -Root $env:TEMP)) {
                throw "Refusing to remove unexpected path: $TempFull"
            }
            if (([IO.Path]::GetFileName($TempFull)) -notlike 'sign-to-speak-install-*') {
                throw "Refusing to remove unexpected folder: $TempFull"
            }
            if (Test-Path -LiteralPath $TempFull) {
                Remove-Item -LiteralPath $TempFull -Recurse -Force -ErrorAction Stop
            }
            Write-Host 'Temporary GitHub download removed.'
        }
        catch {
            Write-Warning "Temporary cleanup failed at ${TempRoot}: $($_.Exception.Message)"
        }
    }
    if ($Incoming -and (Test-Path -LiteralPath $Incoming) -and $env:LOCALAPPDATA) {
        try {
            $IncomingFull = [IO.Path]::GetFullPath($Incoming)
            $IncomingName = [IO.Path]::GetFileName($IncomingFull)
            if ((Test-ChildPath -Path $IncomingFull -Root $env:LOCALAPPDATA) -and
                $IncomingName.StartsWith('SignToSpeak.incoming-')) {
                if ($Destination -and -not (Test-Path -LiteralPath $Destination) -and
                    $Previous -and (Test-Path -LiteralPath $Previous)) {
                    Move-Item -LiteralPath $Previous -Destination $Destination
                }
                Remove-Item -LiteralPath $IncomingFull -Recurse -Force -ErrorAction Stop
                Write-Host 'Incomplete install folder removed.'
            }
        }
        catch {
            Write-Warning "Could not remove incomplete install at ${Incoming}: $($_.Exception.Message)"
        }
    }
}
