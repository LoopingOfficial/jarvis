# Télécharge la chaîne de post-processing Three.js r160 + le décodeur Draco.
#
# Même principe que gltf_fetch.ps1 : `ui/vendor` est gitignoré, les dépendances
# tierces ne sont pas versionnées mais RE-TÉLÉCHARGEABLES à l'identique. Garder
# la version alignée sur three.module.js (r160) est impératif : les passes de
# post-processing touchent aux internes du renderer et cassent d'une version
# à l'autre.
#
#   powershell -ExecutionPolicy Bypass -File ui/vendor/postfx_fetch.ps1
#
# IMPORTANT : les addons officiels vivent dans des sous-dossiers
# (postprocessing/, shaders/) alors que notre ui/vendor est PLAT. Le script
# réécrit donc les imports relatifs vers ./<fichier> — sans cette étape, les
# fichiers se cherchent dans ../shaders/ et le navigateur renvoie du 404.

$base = "https://raw.githubusercontent.com/mrdoob/three.js/r160/examples/jsm"
$out  = Join-Path $PSScriptRoot "."

$files = @{
  "postprocessing/EffectComposer.js"      = "EffectComposer.js"
  "postprocessing/Pass.js"                = "Pass.js"
  "postprocessing/RenderPass.js"          = "RenderPass.js"
  "postprocessing/ShaderPass.js"          = "ShaderPass.js"
  "postprocessing/MaskPass.js"            = "MaskPass.js"
  "postprocessing/UnrealBloomPass.js"     = "UnrealBloomPass.js"
  "postprocessing/OutputPass.js"          = "OutputPass.js"
  "shaders/CopyShader.js"                 = "CopyShader.js"
  "shaders/LuminosityHighPassShader.js"   = "LuminosityHighPassShader.js"
  "shaders/OutputShader.js"               = "OutputShader.js"
  "shaders/FXAAShader.js"                 = "FXAAShader.js"
  "libs/draco/draco_decoder.js"           = "draco/draco_decoder.js"
  "libs/draco/draco_decoder.wasm"         = "draco/draco_decoder.wasm"
  "libs/draco/draco_wasm_wrapper.js"      = "draco/draco_wasm_wrapper.js"
}

New-Item -ItemType Directory -Force -Path (Join-Path $out "draco") | Out-Null

foreach ($k in $files.Keys) {
  $url = "$base/$k"
  $dst = Join-Path $out $files[$k]
  try {
    Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -ErrorAction Stop
    # Aplatissement des imports relatifs (fichiers JS uniquement).
    if ($dst -like "*.js") {
      $content = Get-Content $dst -Raw
      $flat = [regex]::Replace($content, "from '(\.{1,2}/[^']*/)?([A-Za-z0-9_]+\.js)'", "from './`$2'")
      if ($flat -ne $content) { Set-Content -Path $dst -Value $flat -NoNewline }
    }
    Write-Output "OK   $k -> $($files[$k])"
  } catch {
    Write-Output "FAIL $k ($($_.Exception.Message))"
  }
}
Write-Output ""
Write-Output "Chaine disponible : EffectComposer + RenderPass + UnrealBloomPass + OutputPass."
