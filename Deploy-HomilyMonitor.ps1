[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ServiceName = 'homilymonitor',
    [string]$ProjectRoot = $PSScriptRoot,
    [string]$SpecPath = (Join-Path $PSScriptRoot 'homilymonitor.spec'),
    [string]$BuildOutputDir = (Join-Path $PSScriptRoot 'dist\homilymonitor'),
    [string]$DeploymentDir = (Join-Path $PSScriptRoot 'homilymonitorservice'),
    [string]$PythonExe,
    [string]$BackupRoot = (Join-Path $PSScriptRoot 'deploy-backups'),
    [string]$LogPath,
    [switch]$SkipBuild,
    [switch]$SkipServiceRestart,
    [switch]$SyncConfig,
    [switch]$NoBackup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:OriginalScriptBoundParameters = @{} + $PSBoundParameters
$script:LogPath = $null
$script:RelaunchParameters = @{}
$script:ElevatedExecution = $false

function Write-Step {
    param([string]$Message)
    $timestamped = "{0} [HomilyMonitor] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $timestamped
    if ($script:LogPath) {
        Add-Content -LiteralPath $script:LogPath -Value $timestamped
    }
}

function Wait-OnFailureIfNeeded {
    if ($script:ElevatedExecution) {
        try {
            Read-Host "Deployment failed. Press Enter to close this window" | Out-Null
        }
        catch {
        }
    }
}

trap {
    $message = $_.Exception.Message
    if (-not $message) {
        $message = $_.ToString()
    }
    Write-Step "ERROR: $message"
    if ($script:LogPath) {
        Add-Content -LiteralPath $script:LogPath -Value ($_ | Out-String)
    }
    Wait-OnFailureIfNeeded
    exit 1
}

function Resolve-AbsolutePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [string]$BasePath = (Get-Location).Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Start-SelfElevatedCopy {
    param([string]$WorkingDirectory)

    $shellPath = (Get-Process -Id $PID).Path
    if (-not $shellPath) {
        throw "Could not determine the current PowerShell executable for self-elevation."
    }

    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath)
    )

    foreach ($entry in $script:RelaunchParameters.GetEnumerator()) {
        $name = $entry.Key
        $value = $entry.Value

        if ($name -in @('Verbose', 'Debug', 'ErrorAction', 'WarningAction', 'InformationAction')) {
            continue
        }

        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) {
                $argumentList += "-$name"
            }
            continue
        }

        $argumentList += "-$name"
        $argumentList += ('"{0}"' -f ([string]$value).Replace('"', '\"'))
    }

    Write-Step "Relaunching the deployment script as Administrator..."
    try {
        $env:DEPLOY_HOMILYMONITOR_ELEVATED = '1'
        $process = Start-Process -FilePath $shellPath -Verb RunAs -WorkingDirectory $WorkingDirectory -ArgumentList $argumentList -Wait -PassThru
        if ($script:LogPath -and (Test-Path -LiteralPath $script:LogPath)) {
            Write-Host "[HomilyMonitor] Elevated run log: $script:LogPath"
            Get-Content -LiteralPath $script:LogPath -Tail 20 | ForEach-Object { Write-Host $_ }
        }
        exit $process.ExitCode
    }
    catch {
        throw "Self-elevation failed or was cancelled. Run PowerShell as Administrator or allow the UAC prompt."
    }
    finally {
        $env:DEPLOY_HOMILYMONITOR_ELEVATED = $null
    }
}

function Resolve-PythonExecutable {
    param(
        [string]$RequestedPath,
        [string]$RootPath
    )

    if ($RequestedPath) {
        $resolved = Resolve-AbsolutePath -Path $RequestedPath -BasePath $RootPath
        if (-not (Test-Path -LiteralPath $resolved)) {
            throw "Python executable not found: $resolved"
        }
        return $resolved
    }

    $versionFile = Join-Path $RootPath '.python-version'
    if (Test-Path -LiteralPath $versionFile) {
        $version = (Get-Content -LiteralPath $versionFile | Select-Object -First 1).Trim()
        if ($version) {
            $pyenvCandidate = Join-Path $env:USERPROFILE ".pyenv\pyenv-win\versions\$version\python.exe"
            if (Test-Path -LiteralPath $pyenvCandidate) {
                return $pyenvCandidate
            }
        }
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Could not resolve a Python executable. Pass -PythonExe explicitly."
}

function Get-ServiceExecutablePath {
    param([string]$PathName)

    $trimmed = $PathName.Trim()
    if ($trimmed.StartsWith('"')) {
        $endQuote = $trimmed.IndexOf('"', 1)
        if ($endQuote -gt 1) {
            return $trimmed.Substring(1, $endQuote - 1)
        }
    }

    return ($trimmed -split '\s+', 2)[0]
}

function Get-ServiceInstallInfo {
    param([string]$TargetServiceName)

    $service = $null
    try {
        $service = Get-Service -Name $TargetServiceName -ErrorAction Stop
    }
    catch {
        try {
            $service = Get-Service -DisplayName $TargetServiceName -ErrorAction Stop
        }
        catch {
            return $null
        }
    }

    if (-not $service) {
        return $null
    }

    $serviceConfig = & sc.exe qc $service.Name 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query service configuration for '$($service.Name)'."
    }

    $binaryLine = $serviceConfig | Where-Object { $_ -match 'BINARY_PATH_NAME\s*:\s*(.+)$' } | Select-Object -First 1
    if (-not $binaryLine) {
        throw "Could not determine BINARY_PATH_NAME for service '$($service.Name)'."
    }
    if ($binaryLine -notmatch 'BINARY_PATH_NAME\s*:\s*(.+)$') {
        throw "Could not parse BINARY_PATH_NAME for service '$($service.Name)'."
    }

    $binaryPath = Get-ServiceExecutablePath -PathName $Matches[1]
    $binaryDir = [System.IO.Path]::GetDirectoryName($binaryPath)
    $binaryLeaf = [System.IO.Path]::GetFileName($binaryPath)
    $binaryStem = [System.IO.Path]::GetFileNameWithoutExtension($binaryPath)
    $xmlPath = Join-Path $binaryDir ($binaryStem + '.xml')

    $manager = 'SCM'
    if ($binaryLeaf -ieq 'nssm.exe') {
        $manager = 'NSSM'
    }
    elseif (Test-Path -LiteralPath $xmlPath) {
        $manager = 'WinSW'
    }

    [pscustomobject]@{
        Service = $service
        ServiceName = $service.Name
        DisplayName = $service.DisplayName
        BinaryPath = $binaryPath
        BinaryDir = $binaryDir
        WrapperXmlPath = $xmlPath
        Manager = $manager
    }
}

function Wait-ForServiceStatus {
    param(
        [string]$TargetServiceName,
        [ValidateSet('Running', 'Stopped')]
        [string]$DesiredStatus,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $service = Get-Service -Name $TargetServiceName -ErrorAction SilentlyContinue
        if ($service -and $service.Status.ToString() -eq $DesiredStatus) {
            return
        }
        Start-Sleep -Seconds 1
    }

    throw "Service '$TargetServiceName' did not reach status '$DesiredStatus' within $TimeoutSeconds seconds."
}

function Stop-ManagedService {
    param(
        [pscustomobject]$InstallInfo,
        [string]$TargetServiceName
    )

    $service = Get-Service -Name $TargetServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        Write-Step "Service '$TargetServiceName' is not installed. Skipping stop."
        return
    }

    if ($service.Status -eq 'Stopped') {
        Write-Step "Service '$TargetServiceName' is already stopped."
        return
    }

    Write-Step "Stopping service '$TargetServiceName'..."
    switch ($InstallInfo.Manager) {
        'WinSW' {
            & $InstallInfo.BinaryPath stop
            if ($LASTEXITCODE -ne 0) {
                throw "WinSW stop command failed with exit code $LASTEXITCODE."
            }
        }
        default {
            Stop-Service -Name $TargetServiceName -ErrorAction Stop
        }
    }

    Wait-ForServiceStatus -TargetServiceName $TargetServiceName -DesiredStatus 'Stopped'
}

function Start-ManagedService {
    param(
        [pscustomobject]$InstallInfo,
        [string]$TargetServiceName
    )

    $service = Get-Service -Name $TargetServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        throw "Service '$TargetServiceName' is not installed."
    }

    if ($service.Status -eq 'Running') {
        Write-Step "Service '$TargetServiceName' is already running."
        return
    }

    Write-Step "Starting service '$TargetServiceName'..."
    switch ($InstallInfo.Manager) {
        'WinSW' {
            & $InstallInfo.BinaryPath start
            if ($LASTEXITCODE -ne 0) {
                throw "WinSW start command failed with exit code $LASTEXITCODE."
            }
        }
        default {
            Start-Service -Name $TargetServiceName -ErrorAction Stop
        }
    }

    Wait-ForServiceStatus -TargetServiceName $TargetServiceName -DesiredStatus 'Running'
}

function New-DeploymentBackup {
    param(
        [string]$TargetDeploymentDir,
        [string]$TargetBackupRoot,
        [pscustomobject]$InstallInfo
    )

    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupDir = Join-Path $TargetBackupRoot $timestamp
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

    $pathsToBackup = @(
        (Join-Path $TargetDeploymentDir 'homilymonitor.exe'),
        (Join-Path $TargetDeploymentDir '_internal')
    )

    if ($InstallInfo -and $InstallInfo.Manager -eq 'WinSW') {
        $pathsToBackup += $InstallInfo.BinaryPath
        $pathsToBackup += $InstallInfo.WrapperXmlPath
    }

    foreach ($path in $pathsToBackup | Select-Object -Unique) {
        if (Test-Path -LiteralPath $path) {
            Copy-Item -LiteralPath $path -Destination $backupDir -Recurse -Force
        }
    }

    return $backupDir
}

function Assert-BuildOutput {
    param([string]$TargetBuildOutputDir)

    $exePath = Join-Path $TargetBuildOutputDir 'homilymonitor.exe'
    $internalPath = Join-Path $TargetBuildOutputDir '_internal'

    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "Build output missing executable: $exePath"
    }
    if (-not (Test-Path -LiteralPath $internalPath)) {
        throw "Build output missing _internal directory: $internalPath"
    }
}

function Remove-DeploymentArtifacts {
    param([string]$TargetDeploymentDir)

    $exePath = Join-Path $TargetDeploymentDir 'homilymonitor.exe'
    $internalPath = Join-Path $TargetDeploymentDir '_internal'

    if (Test-Path -LiteralPath $exePath) {
        Remove-Item -LiteralPath $exePath -Force
    }
    if (Test-Path -LiteralPath $internalPath) {
        Remove-Item -LiteralPath $internalPath -Recurse -Force
    }
}

$ProjectRoot = Resolve-AbsolutePath -Path $ProjectRoot
$SpecPath = Resolve-AbsolutePath -Path $SpecPath -BasePath $ProjectRoot
$DeploymentDir = Resolve-AbsolutePath -Path $DeploymentDir -BasePath $ProjectRoot
$BackupRoot = Resolve-AbsolutePath -Path $BackupRoot -BasePath $ProjectRoot
$buildSessionId = Get-Date -Format 'yyyyMMdd-HHmmss'
$tempRoot = $env:TEMP
if (-not $tempRoot) {
    $tempRoot = Join-Path $ProjectRoot '.deploy-builds-temp'
}
$buildRoot = Join-Path $tempRoot ("hmdeploy\{0}" -f $buildSessionId)
if (-not $script:OriginalScriptBoundParameters.ContainsKey('BuildOutputDir')) {
    $BuildOutputDir = Join-Path $buildRoot 'dist\homilymonitor'
}
$BuildOutputDir = Resolve-AbsolutePath -Path $BuildOutputDir -BasePath $ProjectRoot
$pyInstallerDistDir = Resolve-AbsolutePath -Path (Split-Path -Parent $BuildOutputDir) -BasePath $ProjectRoot
$pyInstallerWorkDir = Resolve-AbsolutePath -Path (Join-Path $buildRoot 'build') -BasePath $ProjectRoot
if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot ("deploy-logs\deploy-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$LogPath = Resolve-AbsolutePath -Path $LogPath -BasePath $ProjectRoot
$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$script:LogPath = $LogPath
if (-not (Test-Path -LiteralPath $script:LogPath)) {
    New-Item -ItemType File -Path $script:LogPath -Force | Out-Null
}
$script:RelaunchParameters = @{} + $script:OriginalScriptBoundParameters
if (-not $script:RelaunchParameters.ContainsKey('LogPath')) {
    $script:RelaunchParameters['LogPath'] = $LogPath
}
$script:ElevatedExecution = ($env:DEPLOY_HOMILYMONITOR_ELEVATED -eq '1')
$env:DEPLOY_HOMILYMONITOR_ELEVATED = $null

$installInfo = Get-ServiceInstallInfo -TargetServiceName $ServiceName
if ($installInfo) {
    $ServiceName = $installInfo.ServiceName
}
if ($installInfo -and $installInfo.Manager -eq 'WinSW') {
    $DeploymentDir = $installInfo.BinaryDir
}

$requiresElevation = ($installInfo -ne $null) -and (-not $SkipServiceRestart) -and (-not $WhatIfPreference)
if ($requiresElevation -and -not (Test-IsAdministrator)) {
    Start-SelfElevatedCopy -WorkingDirectory $ProjectRoot
}

$python = Resolve-PythonExecutable -RequestedPath $PythonExe -RootPath $ProjectRoot

Write-Step "Project root: $ProjectRoot"
Write-Step "Spec file: $SpecPath"
Write-Step "Build output: $BuildOutputDir"
Write-Step "PyInstaller dist dir: $pyInstallerDistDir"
Write-Step "PyInstaller work dir: $pyInstallerWorkDir"
Write-Step "Deployment directory: $DeploymentDir"
Write-Step "Python executable: $python"
Write-Step "Deployment log: $LogPath"
if ($installInfo) {
    Write-Step "Detected service manager: $($installInfo.Manager)"
}
else {
    Write-Step "Service '$ServiceName' was not detected. Deployment will only update files."
}

if (-not (Test-Path -LiteralPath $DeploymentDir)) {
    throw "Deployment directory not found: $DeploymentDir"
}

$serviceLogDir = Join-Path $DeploymentDir 'logs'
if (-not (Test-Path -LiteralPath $serviceLogDir)) {
    if ($PSCmdlet.ShouldProcess($serviceLogDir, "Create service log directory")) {
        New-Item -ItemType Directory -Path $serviceLogDir -Force | Out-Null
        Write-Step "Ensured service log directory exists: $serviceLogDir"
    }
}

if (-not $SkipBuild) {
    if ($PSCmdlet.ShouldProcess($SpecPath, "Build PyInstaller package")) {
        Write-Step "Building with PyInstaller..."
        if (-not (Test-Path -LiteralPath $pyInstallerDistDir)) {
            New-Item -ItemType Directory -Path $pyInstallerDistDir -Force | Out-Null
        }
        if (-not (Test-Path -LiteralPath $pyInstallerWorkDir)) {
            New-Item -ItemType Directory -Path $pyInstallerWorkDir -Force | Out-Null
        }
        & $python -m PyInstaller --noconfirm --distpath $pyInstallerDistDir --workpath $pyInstallerWorkDir $SpecPath
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller build failed with exit code $LASTEXITCODE."
        }
        $builtExe = Get-Item -LiteralPath (Join-Path $BuildOutputDir 'homilymonitor.exe')
        Write-Step "Build complete: $($builtExe.FullName) updated $($builtExe.LastWriteTime)"
    }
}
else {
    Write-Step "Skipping build."
}

if ($WhatIfPreference) {
    Write-Step "WhatIf mode: skipping build output validation."
}
else {
    Assert-BuildOutput -TargetBuildOutputDir $BuildOutputDir
}

if ($SkipServiceRestart) {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        throw "Service '$ServiceName' is running. Stop it first or rerun without -SkipServiceRestart."
    }
}
elseif ($installInfo) {
    if ($PSCmdlet.ShouldProcess($ServiceName, "Stop Windows service")) {
        Stop-ManagedService -InstallInfo $installInfo -TargetServiceName $ServiceName
    }
}

if (-not $NoBackup) {
    if ($PSCmdlet.ShouldProcess($BackupRoot, "Create deployment backup")) {
        $backupDir = New-DeploymentBackup -TargetDeploymentDir $DeploymentDir -TargetBackupRoot $BackupRoot -InstallInfo $installInfo
        Write-Step "Backup created at: $backupDir"
    }
}
else {
    Write-Step "Skipping backup."
}

if ($PSCmdlet.ShouldProcess($DeploymentDir, "Replace deployed application artifacts")) {
    Write-Step "Updating deployed application artifacts..."
    Remove-DeploymentArtifacts -TargetDeploymentDir $DeploymentDir
    Copy-Item -LiteralPath (Join-Path $BuildOutputDir 'homilymonitor.exe') -Destination $DeploymentDir -Force
    Copy-Item -LiteralPath (Join-Path $BuildOutputDir '_internal') -Destination $DeploymentDir -Recurse -Force
    $deployedExe = Get-Item -LiteralPath (Join-Path $DeploymentDir 'homilymonitor.exe')
    Write-Step "Deployment complete: $($deployedExe.FullName) updated $($deployedExe.LastWriteTime)"
}

$projectConfig = Join-Path $ProjectRoot 'config.json'
$deployConfig = Join-Path $DeploymentDir 'config.json'
if ($SyncConfig) {
    if (-not (Test-Path -LiteralPath $projectConfig)) {
        throw "Cannot sync config because project config.json was not found: $projectConfig"
    }
    if ($PSCmdlet.ShouldProcess($deployConfig, "Copy config.json from project root")) {
        Copy-Item -LiteralPath $projectConfig -Destination $deployConfig -Force
        Write-Step "Deployment config.json was updated from project root."
    }
}
elseif ((-not (Test-Path -LiteralPath $deployConfig)) -and (Test-Path -LiteralPath $projectConfig)) {
    if ($PSCmdlet.ShouldProcess($deployConfig, "Seed missing deployment config.json from project root")) {
        Copy-Item -LiteralPath $projectConfig -Destination $deployConfig -Force
        Write-Step "Deployment config.json was seeded from project root."
    }
}

if (-not $SkipServiceRestart -and $installInfo) {
    if ($PSCmdlet.ShouldProcess($ServiceName, "Start Windows service")) {
        Start-ManagedService -InstallInfo $installInfo -TargetServiceName $ServiceName
    }
}

if ($installInfo) {
    $finalService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($finalService) {
        Write-Step "Final service status: $($finalService.Status)"
    }
}

Write-Step "Deployment complete."
