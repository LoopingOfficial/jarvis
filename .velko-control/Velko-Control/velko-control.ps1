[CmdletBinding()]
param(
  [Parameter(Position=0)] [string]$Command = 'help',
  [Parameter(Position=1)] [string[]]$Args
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg=Join-Path $root 'velko-control.json'
$defaultCfg=@{ allowTitles=@(); requireAllowlist=$false; allowDesktopCapture=$true } | ConvertTo-Json
if(-not (Test-Path $cfg)){ Set-Content $cfg $defaultCfg -Encoding UTF8 }
$config=Get-Content $cfg -Raw | ConvertFrom-Json

if(-not ('Velko.Native' -as [type])){
Add-Type -TypeDefinition @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class VelkoNative {
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
 public delegate bool EnumWindowsProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int cmd);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
 [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] i,int size);
 [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public KEYBDINPUT ki; }
 [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk,scan; public uint flags,time; public IntPtr extra; }
 public static void Unicode(string text){ foreach(char c in text){ INPUT[] a={new INPUT{type=1,ki=new KEYBDINPUT{scan=c,flags=4}},new INPUT{type=1,ki=new KEYBDINPUT{scan=c,flags=6}}}; SendInput(2,a,Marshal.SizeOf(typeof(INPUT))); } }
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
}
'@
}
function Get-Windows { $o=[System.Collections.Generic.List[object]]::new(); [VelkoNative]::EnumWindows({param($h,$p) if([VelkoNative]::IsWindowVisible($h)){ $n=[VelkoNative]::GetWindowTextLength($h); if($n){$s=New-Object Text.StringBuilder ($n+1); [VelkoNative]::GetWindowText($h,$s,$s.Capacity)|Out-Null; $pid=0; [VelkoNative]::GetWindowThreadProcessId($h,[ref]$pid)|Out-Null; [void]$o.Add([pscustomobject]@{hwnd=('0x{0:X}' -f $h.ToInt64()); pid=$pid; title=$s.ToString()}) } }; return $true },[IntPtr]::Zero)|Out-Null; $o }
function Find-Window([string]$title){ $w=Get-Windows|?{$_.title -like "*$title*"}; if(!$w){throw "Fenêtre introuvable: $title"}; if($w.Count -gt 1){$w=$w|select -First 1}; $w }
function Check-Allow([string]$title){ if($config.requireAllowlist -and (-not ($config.allowTitles|?{$title -like "*$_*"}))){throw "Fenêtre refusée par la liste blanche: $title"} }
function Focus([string]$title){$w=Find-Window $title;Check-Allow $w.title;$h=[IntPtr]([Convert]::ToInt64($w.hwnd.Substring(2),16));[VelkoNative]::ShowWindow($h,9)|Out-Null;[VelkoNative]::SetForegroundWindow($h)|Out-Null; [pscustomobject]@{ok=$true;action='focus';title=$w.title;hwnd=$w.hwnd}|ConvertTo-Json -Compress}
function Shot([string]$title,[string]$out){ Add-Type -AssemblyName System.Drawing; if($title){$w=Find-Window $title;Check-Allow $w.title;$h=[IntPtr]([Convert]::ToInt64($w.hwnd.Substring(2),16));$r=New-Object VelkoNative+RECT;[VelkoNative]::GetWindowRect($h,[ref]$r)|Out-Null;$x=$r.L;$y=$r.T;$ww=$r.R-$r.L;$hh=$r.B-$r.T}else{if(-not $config.allowDesktopCapture){throw 'Capture bureau désactivée'};$x=0;$y=0;$ww=[Windows.Forms.Screen]::PrimaryScreen.Bounds.Width;$hh=[Windows.Forms.Screen]::PrimaryScreen.Bounds.Height}; if(!$out){$out=Join-Path (Get-Location) ('velko-shot-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.png')};$b=New-Object Drawing.Bitmap($ww,$hh);$g=[Drawing.Graphics]::FromImage($b);$g.CopyFromScreen($x,$y,0,0,$b.Size);$b.Save($out,[Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$b.Dispose();[pscustomobject]@{ok=$true;path=(Resolve-Path $out).Path}|ConvertTo-Json -Compress }
function Mouse([string]$action,[int]$x,[int]$y){[System.Windows.Forms.Cursor]::Position=New-Object Drawing.Point($x,$y);$f=if($action -eq 'click'){0x2 -bor 0x4}elseif($action -eq 'doubleclick'){0x2 -bor 0x4}else{0};[VelkoNative]::mouse_event($f,$x,$y,0,[UIntPtr]::Zero);if($action -eq 'doubleclick'){Start-Sleep -Milliseconds 80;[VelkoNative]::mouse_event($f,$x,$y,0,[UIntPtr]::Zero)};[pscustomobject]@{ok=$true;action=$action;x=$x;y=$y}|ConvertTo-Json -Compress}
Add-Type -AssemblyName System.Windows.Forms
try { switch($Command.ToLower()){ 'list' {Get-Windows|ConvertTo-Json -Compress} 'focus' {Focus $Args[0]} 'screenshot' {Shot $Args[0] $Args[1]} 'mousemove' {Mouse 'move' $Args[0] $Args[1]} 'click' {Mouse 'click' $Args[0] $Args[1]} 'doubleclick' {Mouse 'doubleclick' $Args[0] $Args[1]} 'type' {$t=$Args -join ' ';[VelkoNative]::Unicode($t);[pscustomobject]@{ok=$true;action='type';chars=$t.Length}|ConvertTo-Json -Compress} 'key' {[System.Windows.Forms.SendKeys]::SendWait($Args[0]);[pscustomobject]@{ok=$true;action='key';key=$Args[0]}|ConvertTo-Json -Compress} 'hotkey' {[System.Windows.Forms.SendKeys]::SendWait($Args[0]);[pscustomobject]@{ok=$true;action='hotkey';keys=$Args[0]}|ConvertTo-Json -Compress} 'wait' {Start-Sleep -Milliseconds ([int]$Args[0]);[pscustomobject]@{ok=$true;action='wait';ms=[int]$Args[0]}|ConvertTo-Json -Compress} default {'Velko Control`n  list`n  focus "title"`n  screenshot ["title"] [out.png]`n  move|click|doubleclick x y`n  type "text"`n  key "{ENTER}"`n  hotkey "^s"`n  wait ms'} } } catch { [Console]::Error.WriteLine((@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress)); exit 1 }
