@echo off
rem Encerra qualquer painel do flow02 que tenha ficado rodando.
rem Mata so processos python cujo comando contem "flow02", nao o Python inteiro.
powershell -NoProfile -Command ^
  "$alvos = Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -match 'flow02' };" ^
  "if (-not $alvos) { Write-Host 'nenhum painel rodando'; exit };" ^
  "foreach ($p in $alvos) { Write-Host \"encerrando PID $($p.ProcessId)\"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue };" ^
  "Start-Sleep -Milliseconds 500; Write-Host 'painel encerrado'"
