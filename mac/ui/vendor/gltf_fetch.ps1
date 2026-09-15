# Télécharge GLTFLoader r160 et ses dépendances depuis le dépôt Three.js.
$base = "https://raw.githubusercontent.com/mrdoob/three.js/r160/examples/jsm"
$out = Join-Path $PSScriptRoot "."
$files = @{
  "loaders/GLTFLoader.js"              = "GLTFLoader.js"
  "loaders/DRACOLoader.js"             = "DRACOLoader.js"
  "loaders/KTX2Loader.js"              = "KTX2Loader.js"
  "loaders/EXRLoader.js"               = "EXRLoader.js"
  "loaders/GLTFParser.js"              = "GLTFParser.js"
  "loaders/RGBELoader.js"              = "RGBELoader.js"
  "utils/BufferGeometryUtils.js"       = "BufferGeometryUtils.js"
  "math/CapsuleGeometry.js"            = "CapsuleGeometry.js"
  "math/ColorManagement.js"            = "ColorManagement.js"
  "math/ColorSpace.js"                 = "ColorSpace.js"
  "loaders/LottieLoader.js"            = "LottieLoader.js"
}
foreach ($k in $files.Keys) {
  $url = "$base/$k"
  $dst = Join-Path $out $files[$k]
  try {
    Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -ErrorAction Stop
    Write-Output "OK  $k -> $($files[$k])"
  } catch {
    Write-Output "FAIL $k ($($_.Exception.Message))"
  }
}