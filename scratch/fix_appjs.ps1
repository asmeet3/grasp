$filePath = Join-Path $PSScriptRoot "..\src\static\app.js"
$filePath = (Resolve-Path $filePath).Path
$lines = [System.IO.File]::ReadAllLines($filePath)
$newLines = New-Object System.Collections.Generic.List[string]
for ($idx = 0; $idx -lt $lines.Count; $idx++) {
    # Skip lines 517-524 (0-indexed: 516-523) - broken leftover code
    if ($idx -ge 516 -and $idx -le 523) { continue }
    $newLines.Add($lines[$idx])
}
[System.IO.File]::WriteAllLines($filePath, $newLines.ToArray())
Write-Host "Removed 8 broken lines (517-524). New line count: $($newLines.Count)"
