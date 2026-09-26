# Velko Worker Windows service (préparation)

La campagne M4-only ne dépend d'aucune commande distante Windows. Après
déploiement du code worker, l'installation initiale peut être automatisée
localement avec une tâche planifiée Windows :

```powershell
$workerRoot = 'C:\Velko\worker'
$python = "$workerRoot\.venv\Scripts\python.exe"
$arguments = '-m jarvis.distributed.worker_service --worker-id rtx3080 --coordinator http://100.120.13.54:8765 --model qwen3.5:9b --capability coding --capability reasoning --capability heavy-task --capability tools'

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $workerRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$restart = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'Velko Worker' -Action $action -Trigger $trigger -Settings $restart -RunLevel Highest -Force
```

Le worker doit écrire ses sorties dans un fichier de log configuré par le
lanceur Windows. La prochaine itération pourra fournir un script signé qui
génère ce lanceur, vérifie `WORKER_PROTOCOL_VERSION`, et effectue une mise à
jour atomique du répertoire avant de redémarrer la tâche. Aucun endpoint Velko
ne doit accepter de commande PowerShell ou de chemin arbitraire.
