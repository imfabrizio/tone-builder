# Tone Builder - avvio su Windows.
# Fa tutto da solo: installa Python se manca, prepara l'ambiente, aggiorna il
# codice e i pacchetti quando servono, crea il collegamento sul desktop e avvia.
param([switch]$SoloInstalla)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Tone Builder"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Venv = Join-Path $env:USERPROFILE ".venvs\tone-builder"
$Py = Join-Path $Venv "Scripts\python.exe"
$Stamp = Join-Path $Venv "pyproject.installed"

function Say($t) { Write-Host $t -ForegroundColor Yellow }
function Pause-End { if (-not $env:TB_NOPAUSE) { Read-Host "Premi Invio per chiudere" | Out-Null } }
function Fail($t) { Write-Host ""; Write-Host $t -ForegroundColor Red; Write-Host ""; Pause-End; exit 1 }

function Find-PyLauncher {
    $cands = @(
        (Get-Command py -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe",
        "$env:WINDIR\py.exe"
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

# 1) aggiorna il codice (se e' una copia git e git e' installato)
if ((Test-Path (Join-Path $Repo ".git")) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Say "Controllo gli aggiornamenti..."
    $ErrorActionPreference = "Continue"
    $before = (git -C $Repo rev-parse --short HEAD 2>$null)
    git -C $Repo fetch -q origin 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Non riesco a scaricare gli aggiornamenti (sei offline?): uso la versione che c'e'." -ForegroundColor Red
    } else {
        git -C $Repo merge --ff-only -q origin/main 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            # file locali modificati per sbaglio (es. fine riga): li metto da parte e allineo al repository
            git -C $Repo stash push -u -q -m "tone-builder: modifiche locali messe da parte" 2>$null | Out-Null
            git -C $Repo reset -q --hard origin/main 2>$null | Out-Null
        }
        $after = (git -C $Repo rev-parse --short HEAD 2>$null)
        if ($after -ne $before) { Say "Aggiornato: versione $after" } else { Say "Gia' aggiornato: versione $after" }
    }
    $ErrorActionPreference = "Stop"
} elseif (-not (Test-Path (Join-Path $Repo ".git"))) {
    Write-Host "Questa copia di Tone Builder non e' collegata a GitHub e non si aggiorna da sola." -ForegroundColor Red
}

# 2) prima installazione, oppure dipendenze cambiate dall'ultima volta
$needInstall = -not (Test-Path $Py)
if (-not $needInstall) {
    $needInstall = -not (Test-Path $Stamp) -or
        ((Get-FileHash (Join-Path $Repo "pyproject.toml")).Hash -ne (Get-Content $Stamp -ErrorAction SilentlyContinue))
}
if ($needInstall) {
    if (-not (Test-Path $Py)) {
        $pyl = Find-PyLauncher
        if (-not $pyl) {
            Say "Python non trovato: lo installo (serve una volta sola)..."
            if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
                Fail "Installa Python 3.12 da https://www.python.org/downloads/ e rilancia Tone Builder."
            }
            winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
            $pyl = Find-PyLauncher
            if (-not $pyl) { Fail "Python installato: chiudi questa finestra e rilancia Tone Builder." }
        }
        Say "Preparo l'ambiente di Tone Builder (solo la prima volta, qualche minuto)..."
        try { & $pyl -3.12 -m venv $Venv 2>$null } catch { }
        if (-not (Test-Path $Py)) { try { & $pyl -3 -m venv $Venv } catch { } }
        if (-not (Test-Path $Py)) { Fail "Non riesco a creare l'ambiente Python in $Venv" }
    } else {
        Say "Aggiorno i componenti di Tone Builder..."
    }
    & $Py -m pip install -q --upgrade pip
    & $Py -m pip install -q -e ".[all]" "yt-dlp[default]" librosa
    if ($LASTEXITCODE -ne 0) { Fail "Installazione dei componenti non riuscita: leggi l'errore qui sopra." }
    (Get-FileHash (Join-Path $Repo "pyproject.toml")).Hash | Set-Content $Stamp
    Say "Componenti pronti."
}

# 3) collegamento sul desktop (lo crea o lo aggiorna)
try {
    $desk = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desk "Tone Builder.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $Repo "avvia.bat"
    $s.WorkingDirectory = $Repo
    $s.IconLocation = (Join-Path $Repo "packaging\tonebuilder.ico") + ",0"
    $s.Description = "Tone Builder"
    $s.Save()
} catch { }

if ($SoloInstalla) {
    Say "Fatto. Da ora avvia Tone Builder dall'icona sul desktop."
    Pause-End
    exit 0
}

# 4) avvio
Write-Host ""
Write-Host "Tone Builder si apre nel browser. Lascia aperta questa finestra mentre lo usi; chiudila per uscire." -ForegroundColor Green
Write-Host ""
& $Py -m tonebuilder
if ($LASTEXITCODE -ne 0) { Fail "Tone Builder si e' chiuso con un errore (vedi sopra)." }
