param([string]$Directory = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Directory = [IO.Path]::GetFullPath($Directory)
$m = Get-Content -LiteralPath (Join-Path $Directory 'sign-parts.json') -Raw | ConvertFrom-Json
if ($m.output_name -ne 'Sign-phoneQa-unsigned.apk' -or $m.parts.Count -ne 3 -or $m.bytes -ne 23682746 -or $m.sha256 -ne '60d255d02a3b44520e024796eda455dd0670af5b3b6aab06aa964dbb9aceaefc') { throw 'Unexpected manifest identity' }
$out = Join-Path $Directory $m.output_name
if (Test-Path -LiteralPath $out) { throw 'Output already exists; no overwrite allowed' }
$i = 0
foreach ($p in $m.parts) {
  $i++
  if ($p.name -ne ('Sign-phoneQa-unsigned.apk.part{0:D3}' -f $i)) { throw 'Unexpected part path/order' }
  $part = Join-Path $Directory $p.name
  if ((Get-Item -LiteralPath $part).Length -ne $p.bytes -or (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $p.sha256) { throw ('Part verification failed: ' + $p.name) }
}
$temp = Join-Path $Directory ('Sign-reassembly-' + [Guid]::NewGuid().ToString('N') + '.partial')
$stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
  foreach ($p in $m.parts) {
    $input = [IO.File]::OpenRead((Join-Path $Directory $p.name))
    try { $input.CopyTo($stream) } finally { $input.Dispose() }
  }
} finally { $stream.Dispose() }
if ((Get-Item -LiteralPath $temp).Length -ne $m.bytes -or (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash.ToLowerInvariant() -ne $m.sha256) { throw ('Whole-file verification failed; inspect temporary file: ' + $temp) }
[IO.File]::Move($temp, $out)
Write-Output ('Verified unsigned ARM64 APK: ' + $out)
Write-Output ('SHA256: ' + $m.sha256)
Write-Output 'No signing or installation was performed.'
