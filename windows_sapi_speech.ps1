param(
    [Parameter(Mandatory=$true)][ValidateSet('List','Speak')][string]$Action,
    [string]$ConfigPath
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Action -eq 'List') {
        $names = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name })
        ConvertTo-Json -InputObject $names -Compress
        exit 0
    }
    if (-not $ConfigPath) { throw 'Missing speech configuration.' }
    $cfg = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $synth.SelectVoice([string]$cfg.voice)
    $rate = [int][Math]::Round(([double]$cfg.speed - 1.0) * 10.0)
    $synth.Rate = [Math]::Max(-10,[Math]::Min(10,$rate))
    $synth.Volume = [Math]::Max(0,[Math]::Min(100,[int][Math]::Round([double]$cfg.volume * 100.0)))
    $synth.SetOutputToWaveFile([string]$cfg.output)
    $synth.Speak([string]$cfg.text)
    $synth.SetOutputToNull()
} finally {
    $synth.Dispose()
}
