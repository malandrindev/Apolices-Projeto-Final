# Setup portatil: nenhum instalador e executado e nenhum registro/PATH global e alterado.
# Binarios: https://www.7-zip.org/download.html e manifesto oficial Microsoft WinGet.
# Idiomas: https://github.com/tesseract-ocr/tessdata_fast no commit fixado abaixo.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ocrProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ocrToolsRoot = [System.IO.Path]::GetFullPath((Join-Path $ocrProjectRoot 'data/processed/tooling'))
$ocrExpectedPrefix = $ocrProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $ocrToolsRoot.StartsWith($ocrExpectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'O destino dos downloads deve permanecer dentro do repositorio.'
}
New-Item -ItemType Directory -Path $ocrToolsRoot -Force | Out-Null

function Get-VerifiedOcrDownload {
    param([string]$Uri, [string]$Destination, [string]$Sha256)
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
            return
        }
    }
    $ocrTemporaryDownload = $Destination + '.download'
    Invoke-WebRequest -Uri $Uri -OutFile $ocrTemporaryDownload -UseBasicParsing -TimeoutSec 60
    if ((Get-FileHash -LiteralPath $ocrTemporaryDownload -Algorithm SHA256).Hash -ne $Sha256) {
        throw ('SHA-256 invalido para ' + [System.IO.Path]::GetFileName($Destination))
    }
    Move-Item -LiteralPath $ocrTemporaryDownload -Destination $Destination -Force
}

$ocrDownloads = @(
    @{
        name = '7zr.exe'
        uri = 'https://github.com/ip7z/7zip/releases/download/26.03/7zr.exe'
        sha256 = 'AD4C82FADCBDF93C03B4FC440F300509C7D60C5C2F4D183E35D9D70D6957037D'
    },
    @{
        name = '7z2603-x64.exe'
        uri = 'https://github.com/ip7z/7zip/releases/download/26.03/7z2603-x64.exe'
        sha256 = '0859C524B8A63551848F0C246ABDDCB1D0B7B656B0FBFE879F8D85E61A9E6EDD'
    },
    @{
        name = 'tesseract-ocr-w64-setup-5.4.0.20240606.exe'
        uri = 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe'
        sha256 = 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9'
    }
)
foreach ($ocrDownload in $ocrDownloads) {
    Get-VerifiedOcrDownload -Uri $ocrDownload.uri -Destination (Join-Path $ocrToolsRoot $ocrDownload.name) -Sha256 $ocrDownload.sha256
}

$ocrSevenZipRoot = Join-Path $ocrToolsRoot '7zip'
$ocrEngineRoot = Join-Path $ocrToolsRoot 'tesseract'
# Os arquivos EXE de setup sao tratados como arquivos compactados; nunca iniciados.
& (Join-Path $ocrToolsRoot '7zr.exe') x (Join-Path $ocrToolsRoot '7z2603-x64.exe') ("-o" + $ocrSevenZipRoot) -y
if ($LASTEXITCODE -gt 1) { throw 'Falha ao extrair os binarios portateis do 7-Zip.' }
& (Join-Path $ocrSevenZipRoot '7z.exe') x (Join-Path $ocrToolsRoot $ocrDownloads[2].name) ("-o" + $ocrEngineRoot) -y
if ($LASTEXITCODE -gt 1) { throw 'Falha ao extrair os binarios portateis do Tesseract.' }

$ocrDataRoot = Join-Path $ocrEngineRoot 'tessdata'
New-Item -ItemType Directory -Path $ocrDataRoot -Force | Out-Null
$ocrModelCommit = '87416418657359cb625c412a48b6e1d6d41c29bd'
$ocrModelHashes = @{
    eng = '7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'
    por = 'C4932B937207A9514B7514D518B931A99938C02A28A5A5A553F8599ED58B7DEB'
}
foreach ($ocrLanguage in @('eng', 'por')) {
    Get-VerifiedOcrDownload -Uri ("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/" + $ocrModelCommit + '/' + $ocrLanguage + '.traineddata') -Destination (Join-Path $ocrDataRoot ($ocrLanguage + '.traineddata')) -Sha256 $ocrModelHashes[$ocrLanguage]
}

$ocrEngineExecutable = Join-Path $ocrEngineRoot 'tesseract.exe'
& $ocrEngineExecutable --version
if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel executar o Tesseract portatil.' }
$ocrInstalledLanguages = & $ocrEngineExecutable --list-langs
if ($LASTEXITCODE -ne 0 -or $ocrInstalledLanguages -notcontains 'eng' -or $ocrInstalledLanguages -notcontains 'por') {
    throw 'A engine portatil nao encontrou os idiomas eng e por.'
}
$ocrInstalledLanguages | Write-Output
$ocrMetadata = @{
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    engine_version = '5.4.0.20240606'
    executable = $ocrEngineExecutable
    languages = @('eng', 'por')
    downloads = $ocrDownloads
    model_repository = 'https://github.com/tesseract-ocr/tessdata_fast'
    model_commit = $ocrModelCommit
    model_sha256 = $ocrModelHashes
    installer_manifest = 'https://github.com/microsoft/winget-pkgs/blob/master/manifests/u/UB-Mannheim/TesseractOCR/5.4.0.20240606/UB-Mannheim.TesseractOCR.installer.yaml'
    sevenzip_release_metadata = 'https://api.github.com/repos/ip7z/7zip/releases/tags/26.03'
}
$ocrMetadata | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ocrToolsRoot 'tesseract_setup.json') -Encoding UTF8
Write-Output ('TESSERACT_CMD=' + $ocrEngineExecutable)
