$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $root '.qwen3tts-env'
$enginePath = Join-Path $root 'qwen3tts-engine'
$logPath = Join-Path $root 'clone_setup.log'
$tempPath = Join-Path $env:TEMP ('chatvoice-qwen3tts-' + [guid]::NewGuid().ToString('N'))
$zipPath = Join-Path $tempPath 'qwen3tts-source.zip'
try { Start-Transcript -Path $logPath -Force | Out-Null } catch {}

function Test-PyVersion($version) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & py "-$version" --version 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
    finally { $ErrorActionPreference = $old }
}
function Test-Nvidia {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & nvidia-smi 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
    finally { $ErrorActionPreference = $old }
}

try {
    Write-Host 'Looking for Python 3.12 (the version the cloning engine is made for)...'
    $version = $null
    if (Test-PyVersion '3.12') { $version = '3.12' }
    if (-not $version -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Python 3.12 was not found. Installing it with winget (free, installs for your user only)...'
        & winget install --id Python.Python.3.12 -e --silent --scope user --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            & winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
        }
        if (Test-PyVersion '3.12') { $version = '3.12' }
    }
    if (-not $version) {
        foreach ($candidate in '3.11', '3.13') {
            if (Test-PyVersion $candidate) {
                $version = $candidate
                Write-Host ('Python 3.12 is not available, so Python ' + $candidate + ' will be tried instead. This is less tested and may fail.') -ForegroundColor Yellow
                break
            }
        }
    }
    if (-not $version) {
        throw 'No suitable Python was found. Install Python 3.12 from https://www.python.org/downloads/ (keep the "py launcher" option ticked), close this window, and run the setup again.'
    }
    & py "-$version" --version

    $python = Join-Path $envPath 'Scripts\python.exe'
    if (Test-Path $envPath) {
        $healthy = $false
        if (Test-Path $python) {
            try { & $python --version 2>&1 | Out-Null; $healthy = ($LASTEXITCODE -eq 0) } catch {}
        }
        if (-not $healthy) {
            Write-Host 'Removing a broken environment from an earlier setup...'
            Remove-Item -LiteralPath $envPath -Recurse -Force
        }
    }
    if (-not (Test-Path $python)) {
        Write-Host 'Creating a separate local environment...'
        & py "-$version" -m venv $envPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }

    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }

    if (Test-Nvidia) {
        Write-Host 'NVIDIA graphics card found. Installing PyTorch with CUDA (this is a large download)...'
        & $python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
    } else {
        Write-Host 'No NVIDIA graphics card was found. Installing the CPU version of PyTorch. Cloning will work but be slow, and fine-tuning is not possible.' -ForegroundColor Yellow
        & $python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not install PyTorch.' }

    Write-Host 'Installing Qwen3-TTS and its helpers...'
    & $python -m pip install -U qwen-tts soundfile numpy faster-whisper accelerate tensorboard
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the cloning engine. The messages above say which package failed.' }

    # SoX is an optional audio helper that qwen-tts looks for. Failing to install it is not fatal.
    if (-not (Get-Command sox -ErrorAction SilentlyContinue) -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'Installing SoX (optional audio helper)...'
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & winget install --id ChrisBagwell.SoX -e --silent --accept-package-agreements --accept-source-agreements
        $ErrorActionPreference = $old
    }

    # The official fine-tuning scripts are only used by the "Fine-tune a voice" feature.
    if (-not (Test-Path (Join-Path $enginePath 'finetuning\sft_12hz.py'))) {
        if (Test-Path $enginePath) {
            $aside = $enginePath + '.old-' + (Get-Date -Format 'yyyyMMddHHmmss')
            Write-Host ('Moving an incomplete earlier download aside: ' + $aside)
            Move-Item -LiteralPath $enginePath -Destination $aside
        }
        Write-Host 'Downloading the Qwen3-TTS fine-tuning scripts...'
        New-Item -ItemType Directory -Path $tempPath -Force | Out-Null
        Invoke-WebRequest -Uri 'https://github.com/QwenLM/Qwen3-TTS/archive/refs/heads/main.zip' -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $tempPath -Force
        $source = Join-Path $tempPath 'Qwen3-TTS-main'
        if (-not (Test-Path (Join-Path $source 'finetuning\sft_12hz.py'))) { throw 'The Qwen3-TTS source archive did not have the expected fine-tuning folder.' }
        Move-Item -LiteralPath $source -Destination $enginePath
    }

    Write-Host 'Checking the installation...'
    & $python -c "import torch; from qwen_tts import Qwen3TTSModel; print('Qwen3-TTS loaded. GPU available:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"
    if ($LASTEXITCODE -ne 0) { throw 'Qwen3-TTS installed but could not be loaded. The messages above say why.' }

    Write-Host ''
    Write-Host 'Setup complete. The voice model (several GB) downloads automatically the first time you use a cloned voice, so that first use is slow.' -ForegroundColor Green
} catch {
    Write-Host ''
    Write-Host ('Setup failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('A copy of this window is saved in: ' + $logPath)
} finally {
    if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    if (Test-Path $tempPath) { Remove-Item -LiteralPath $tempPath -Recurse -Force }
    try { Stop-Transcript | Out-Null } catch {}
}
Read-Host 'Press Enter to close this setup window'
