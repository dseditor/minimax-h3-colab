<#
  H3 digital human: drive a ComfyUI server (Colab tunnel URL or local) through its HTTP API.
  Works on Windows PowerShell 5.1 and PowerShell 7. Uses curl.exe (built into Windows 10/11) for uploads/downloads.

  Check connection only:
    powershell -ExecutionPolicy Bypass -File h3_colab.ps1 -Url https://xxxx.trycloudflare.com -Check
  Generate:
    powershell -ExecutionPolicy Bypass -File h3_colab.ps1 -Url <URL> -Image photo.jpg -PromptFile prompt.txt -Seconds 10 -Out result.mp4
#>
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [switch]$Check,
  [string]$Image,
  [string]$PromptFile,
  [int]$Seconds = 10,
  [double]$Megapixels = 0.4,
  [long]$Seed = -1,
  [string]$Out = "h3_result.mp4",
  [int]$TimeoutMinutes = 30
)
$ErrorActionPreference = 'Stop'
$Url = $Url.TrimEnd('/')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1) connection check
try {
  $stats = Invoke-RestMethod -Uri "$Url/system_stats" -TimeoutSec 20
  $dev = $stats.devices | Select-Object -First 1
  Write-Host ("[OK] connected: {0}, VRAM {1:N1} GiB" -f $dev.name, ($dev.vram_total / 1GB))
} catch {
  Write-Host "[FAIL] cannot reach $Url/system_stats"
  Write-Host "       Check that Colab cells 2 and 3-b are still running and the URL is correct."
  exit 2
}
if ($Check) { exit 0 }

if (-not $Image -or -not (Test-Path $Image)) { throw "Image not found: $Image" }
if (-not $PromptFile -or -not (Test-Path $PromptFile)) { throw "Prompt file not found: $PromptFile" }
if ($Seconds -lt 5 -or $Seconds -gt 15) { throw "Seconds must be 5-15" }

# 2) upload image
$up = & curl.exe -s -F "image=@$((Resolve-Path $Image).Path)" -F "overwrite=true" "$Url/upload/image" | ConvertFrom-Json
if (-not $up.name) { throw "Upload failed" }
$imgName = if ($up.subfolder) { "$($up.subfolder)/$($up.name)" } else { $up.name }
Write-Host "[OK] uploaded image: $imgName"

# 3) fill the workflow template
$prompt = [IO.File]::ReadAllText((Resolve-Path $PromptFile).Path, [Text.Encoding]::UTF8).Trim()
$wf = [IO.File]::ReadAllText((Join-Path $here '..\assets\workflow_api.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$wf.'7'.inputs.prompt = $prompt
$wf.'5'.inputs.image = $imgName
$wf.'56'.inputs.value = $Seconds
$wf.'61'.inputs.megapixels = $Megapixels
if ($Seed -lt 0) { $Seed = Get-Random -Minimum 0 -Maximum 2147483647 }
$wf.'54'.inputs.seed = $Seed
$body = @{ prompt = $wf } | ConvertTo-Json -Depth 30 -Compress
$resp = Invoke-RestMethod -Uri "$Url/prompt" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$promptId = $resp.prompt_id
Write-Host "[OK] queued $promptId (seed $Seed, $Seconds s). Words in prompt: $(($prompt -split '\s+').Count)"

# 4) wait
$t0 = Get-Date
while ($true) {
  Start-Sleep -Seconds 10
  $h = Invoke-RestMethod -Uri "$Url/history/$promptId" -TimeoutSec 30
  $entry = $h.$promptId
  if ($entry) {
    if ($entry.status.status_str -eq 'error') { $entry.status | ConvertTo-Json -Depth 10 | Write-Host; throw "Generation failed" }
    break
  }
  if (((Get-Date) - $t0).TotalMinutes -gt $TimeoutMinutes) { throw "Timed out" }
  Write-Host ("  ... {0:N0} s" -f ((Get-Date) - $t0).TotalSeconds)
}

# 5) download
$v = $entry.outputs.'16'.images | Select-Object -First 1
$q = "filename=$([uri]::EscapeDataString($v.filename))&subfolder=$([uri]::EscapeDataString($v.subfolder))&type=$($v.type)"
& curl.exe -s -o $Out "$Url/view?$q"
if (-not (Test-Path $Out) -or (Get-Item $Out).Length -eq 0) { throw "Download failed" }
Write-Host ("[DONE] {0} ({1:N1} MB) in {2:N1} min" -f (Resolve-Path $Out).Path, ((Get-Item $Out).Length / 1MB), ((Get-Date) - $t0).TotalMinutes)
