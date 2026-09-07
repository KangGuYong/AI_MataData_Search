# sqlmcp -> uvicorn -> (선택) streamlit 순으로 띄우고, 종료 시 함께 내린다.
# 사용법: scripts\run.ps1 api   또는   scripts\run.ps1 ui
param([ValidateSet("api", "ui")][string]$Mode = "ui")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Wait-For([string]$url, [string]$name, [int[]]$okCodes) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $code = (Invoke-WebRequest -Uri $url -TimeoutSec 2).StatusCode
            if ($okCodes -contains $code) { Write-Host "OK $name ($code)"; return }
        } catch {
            # Windows PowerShell 5.1 에는 -SkipHttpErrorCheck 가 없어 4xx/5xx 응답이
            # 예외로 던져진다. 여기서 상태 코드를 꺼내 정상 처리 경로와 합류시킨다.
            $code = $null
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
            if ($code -and $okCodes -contains $code) { Write-Host "OK $name ($code)"; return }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$name 기동 실패: $url"
}

$procs = @()
try {
    $procs += Start-Process python -ArgumentList "-m", "sqlmcp.server" -PassThru -NoNewWindow
    # 인증이 켜져 있으므로 토큰 없는 요청에 401 이 오는 것이 정상이다.
    Wait-For "http://127.0.0.1:8100/mcp" "sqlmcp" @(401)

    $procs += Start-Process python -ArgumentList "-m", "uvicorn", "app.api:api", "--port", "8000" -PassThru -NoNewWindow
    Wait-For "http://127.0.0.1:8000/docs" "uvicorn" @(200)

    if ($Mode -eq "ui") {
        python -m streamlit run app/ui.py
    } else {
        Write-Host "API 준비 완료. Ctrl+C 로 종료합니다."
        Wait-Process -Id $procs[-1].Id
    }
} finally {
    foreach ($p in $procs) {
        if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
}
