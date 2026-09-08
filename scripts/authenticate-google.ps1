param([string]$ProjectId,[switch]$SkipApplicationDefault)
$ErrorActionPreference = 'Stop'
Write-Host 'A browser window will open for Google account authentication.'
Write-Host 'This script never prints or stores passwords, tokens, or secret values.'
$gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloud) { throw 'gcloud CLI was not found. Install the Google Cloud CLI and run this script again.' }
& $gcloud.Source auth login
if ($LASTEXITCODE -ne 0) { throw 'gcloud auth login failed' }
if (-not $SkipApplicationDefault) {
    Write-Host 'A second browser window will open for Application Default Credentials.'
    & $gcloud.Source auth application-default login
    if ($LASTEXITCODE -ne 0) { throw 'gcloud application-default login failed' }
}
if ($ProjectId) {
    & $gcloud.Source config set project $ProjectId
    if ($LASTEXITCODE -ne 0) { throw 'gcloud project selection failed' }
}
Write-Host 'Authenticated account(s):'
& $gcloud.Source auth list
