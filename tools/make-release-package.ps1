<#
  tools/make-release-package.ps1
  ====================================================================
  生成可直接当作 GitHub Release 附件上传的两个 zip。

  用法（PowerShell）：
      powershell -ExecutionPolicy Bypass -File tools\make-release-package.ps1

  产物（放进 dist/，该目录已在 .gitignore 里，不进仓库）：
      dist/one-click-to-qq-extension-v<版本>.zip   仅浏览器扩展
      dist/one-click-to-qq-kit-v<版本>.zip         扩展 + 本地桥（完整包）

  设计要点：
  · 版本号从 extension/manifest.json 读取，**不硬编码** —— 免得上一个版本
    的号留在包里（本项目 make-preview.js 曾硬编码版本号而谎报过版本）。
  · 读 manifest 用正则而非 ConvertFrom-Json —— 本机 PowerShell 读 UTF-8
    中文会乱码，ConvertFrom-Json 会直接报 JSON_BAD（假故障）。
  · 打包后**必做两道检查**：① 敏感文件名（config.json / state.json /
    日志 / pycache）② 明文密钥（AppSecret / AppID / openid）。任一中招
    就不生成，直接抛错。
  · zip 内顶层带一个同名文件夹，解压出来是干净的独立目录，
    不会把文件撒一桌子（Chrome「加载已解压的扩展程序」要选文件夹）。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$root    = Split-Path -Parent $PSScriptRoot          # 仓库根
$extDir  = Join-Path $root "extension"
$brDir   = Join-Path $root "bridge"
$pkgDir  = Join-Path $root "packaging"
$distDir = Join-Path $root "dist"
$report  = Join-Path $root "tools\make-release-package.txt"

$log = New-Object System.Collections.Generic.List[string]
function Say($s) { $log.Add($s) }

# ---------------------------------------------------------------- 版本号
$mfText = Get-Content -LiteralPath (Join-Path $extDir "manifest.json") -Raw -Encoding UTF8
$mm = [regex]::Match($mfText, '"version"\s*:\s*"([^"]+)"')
if (-not $mm.Success) { throw "读不到 extension/manifest.json 里的 version 字段" }
$ver = $mm.Groups[1].Value
Say "版本号（读自 manifest.json）= $ver"

# ---------------------------------------------------------------- 准备
New-Item -ItemType Directory -Path $distDir -Force | Out-Null
$stage = Join-Path $env:TEMP ("qqpkg-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$extPkgName = "one-click-to-qq-extension-v$ver"
$kitPkgName = "one-click-to-qq-kit-v$ver"
$guideInZip = "!安装说明-先看这个.txt"     # ! 让它排在解压后目录的最上面

# 说明文件里的版本号不写死，用 {{VER}} 占位，这里按 manifest 现填。
# 为什么不用"把所有 vX.Y.Z 都替换掉"那种粗暴做法：说明里有一句
# 「发图片（v1.0.1 起）」—— 那是**历史事实**，替换掉就变成假的。
function Copy-Guide($srcPath, $destPath) {
  $t = Get-Content -LiteralPath $srcPath -Raw -Encoding UTF8
  $t = $t.Replace("{{VER}}", $ver)
  if ($t.Contains("{{VER}}")) { throw ("说明文件里还有没填上的占位符：" + $srcPath) }
  # 保持和源文件一致：UTF-8 不带 BOM
  [System.IO.File]::WriteAllText($destPath, $t, (New-Object System.Text.UTF8Encoding $false))
}

# ---------------------------------------------------------------- 扩展包
Say ""
Say "=== 组装扩展包 ==="
$d1 = Join-Path $stage $extPkgName
New-Item -ItemType Directory -Path $d1 -Force | Out-Null
Copy-Item -Path (Join-Path $extDir "*") -Destination $d1 -Recurse -Force
Copy-Guide (Join-Path $pkgDir "扩展版-安装说明.txt") (Join-Path $d1 $guideInZip)
Get-ChildItem $d1 -Recurse -File | ForEach-Object { Say ("  " + $_.FullName.Replace($stage, "") + "  " + $_.Length) }

$zip1 = Join-Path $distDir "$extPkgName.zip"
Remove-Item -LiteralPath $zip1 -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $d1 -DestinationPath $zip1 -CompressionLevel Optimal -Force
Say ("  -> " + $zip1 + "  " + (Get-Item $zip1).Length + " bytes")

# ---------------------------------------------------------------- 完整包
Say ""
Say "=== 组装完整包（扩展 + 桥）==="
# ⚠️ 这三个（1.0.4 新增）不是可选的：少了它们，装完整包的人点弹窗上那个
#    「启动本地桥」按钮会永远失败 —— 宿主入口、宿主逻辑、兜底登记脚本，
#    缺一个那条链就断。所以它们必须留在这张白名单里。
$kitFiles = @("qq_bridge.py", "send_test.py", "probe_url.py", "start_bridge.bat",
              "config.example.json",
              "qq_host.bat", "qq_native_host.py", "重新登记.bat")
$d2 = Join-Path $stage $kitPkgName
New-Item -ItemType Directory -Path $d2 -Force | Out-Null
Copy-Item -Path $extDir -Destination (Join-Path $d2 "extension") -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $d2 "bridge") -Force | Out-Null
foreach ($f in $kitFiles) {
  # 白名单式拷贝：只拷这几个文件，bridge 目录里的日志/凭据一概不碰
  Copy-Item -LiteralPath (Join-Path $brDir $f) -Destination (Join-Path $d2 "bridge") -Force
}
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $d2 -Force
Copy-Guide (Join-Path $pkgDir "完整版-安装说明.txt") (Join-Path $d2 $guideInZip)
Get-ChildItem $d2 -Recurse -File | ForEach-Object { Say ("  " + $_.FullName.Replace($stage, "") + "  " + $_.Length) }

$zip2 = Join-Path $distDir "$kitPkgName.zip"
Remove-Item -LiteralPath $zip2 -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $d2 -DestinationPath $zip2 -CompressionLevel Optimal -Force
Say ("  -> " + $zip2 + "  " + (Get-Item $zip2).Length + " bytes")

# ---------------------------------------------------------------- 打包后检查
Say ""
Say "=== 打包后检查 ==="

# 先取出"绝不该出现在包里"的实际值。
# ⚠️ 千万不要把密钥字面量写死在这个脚本里 —— 脚本是要提交进**公开**仓库的，
#    写死等于把 AppSecret 直接公布出去。（本脚本第一版就这么错过，幸好提交前抓到。）
#    正确做法：运行时从本地 bridge/config.json 与 bridge/state.json 现读那两个值，
#    这两个文件本身在 .gitignore 里，永远不会进仓库。
$secretValues = @()
foreach ($cf in @((Join-Path $brDir "config.json"), (Join-Path $brDir "state.json"))) {
  if (Test-Path -LiteralPath $cf) {
    $raw = Get-Content -LiteralPath $cf -Raw -Encoding UTF8
    foreach ($k in @("appid", "secret", "openid")) {
      $mk = [regex]::Match($raw, '"' + $k + '"\s*:\s*"([^"]+)"')
      if ($mk.Success -and $mk.Groups[1].Value.Length -ge 6) { $secretValues += $mk.Groups[1].Value }
    }
  }
}
$secretValues = @($secretValues | Select-Object -Unique)
if ($secretValues.Count) {
  Say ("  比对值：从本地凭据文件读到 $($secretValues.Count) 项（只报数量，不打印内容）")
} else {
  Say "  比对值：本地没有 config.json / state.json，明文比对将跳过"
}

$checkDir = Join-Path $stage "_unzip"
$fail = $false
foreach ($z in @($zip1, $zip2)) {
  $dest = Join-Path $checkDir ([System.IO.Path]::GetFileNameWithoutExtension($z))
  New-Item -ItemType Directory -Path $dest -Force | Out-Null
  Expand-Archive -LiteralPath $z -DestinationPath $dest -Force
  $name = [System.IO.Path]::GetFileName($z)
  Say ""
  Say "--- $name ---"

  # ① 敏感文件名
  $bad = Get-ChildItem $dest -Recurse -File | Where-Object {
    $_.Name -match "^config\.json$" -or $_.Name -match "^state\.json$" -or
    $_.Extension -eq ".log" -or $_.Extension -eq ".pyc" -or $_.FullName -match "__pycache__"
  }
  if ($bad) {
    Say "  [FAIL] 含有不该打包的文件："
    $bad | ForEach-Object { Say ("         " + $_.FullName.Replace($dest, "")) }
    $fail = $true
  } else {
    Say "  [OK]   无 config.json / state.json / 日志 / pycache"
  }

  # ② 明文密钥（用上面现读出来的值比对，脚本里不含任何密钥字面量）
  $scan = Get-ChildItem $dest -Recurse -File -Include *.py, *.js, *.json, *.md, *.html, *.bat, *.txt
  if ($secretValues.Count) {
    $hit = $scan | Select-String -Pattern $secretValues -SimpleMatch -ErrorAction SilentlyContinue
    if ($hit) {
      Say "  [FAIL] 发现明文凭据："
      $hit | ForEach-Object { Say ("         " + $_.Filename + ":" + $_.LineNumber) }
      $fail = $true
    } else {
      Say "  [OK]   无 AppSecret / AppID / openid 明文"
    }
  } else {
    Say "  [SKIP] 本地无凭据文件可比对（仅做了文件名检查）"
  }

  # ③ 扩展本体必须完整
  # 注意路径：zip 内**顶层还有一层同名文件夹**（解压出来才干净），
  # 完整包里扩展是它下面的 extension/ 子目录。这里最容易写错，
  # 曾经漏掉顶层那层导致 9 个文件全部误报缺失。
  $need = @("manifest.json", "background.js", "content.js", "popup.html", "popup.js",
            "imageutil.js",
            "icons\icon16.png", "icons\icon32.png", "icons\icon48.png", "icons\icon128.png")
  $topName = [System.IO.Path]::GetFileNameWithoutExtension($z)   # = zip 内顶层文件夹名
  $isKit = $topName -notlike "*extension*"
  $base = Join-Path $dest $topName
  if ($isKit) { $base = Join-Path $base "extension" }
  $miss = $need | Where-Object { -not (Test-Path -LiteralPath (Join-Path $base $_)) }
  if ($miss) {
    Say ("  [FAIL] 扩展缺少文件：" + ($miss -join ", "))
    $fail = $true
  } else {
    Say ("  [OK]   扩展 " + $need.Count + " 个必需文件齐全（含 4 个尺寸图标）")
  }

  # ④ 包里的安装说明必须写着当前版本
  # 以前版本号在说明文件里是写死的，每次发版都得手改、漏了就会让包里的说明书
  # 指着一个不存在的 zip 名。现在改成 {{VER}} 占位符，这道检查保证它真被填上了。
  $guide = Join-Path $dest (Join-Path $topName $guideInZip)
  if (-not (Test-Path -LiteralPath $guide)) {
    Say "  [FAIL] 包里找不到安装说明"
    $fail = $true
  } else {
    $gt = Get-Content -LiteralPath $guide -Raw -Encoding UTF8
    if ($gt.Contains("{{VER}}")) {
      Say "  [FAIL] 安装说明里还有没填上的占位符"
      $fail = $true
    } elseif ($gt -notmatch ("v" + [regex]::Escape($ver))) {
      Say ("  [FAIL] 安装说明里没写当前版本 v" + $ver)
      $fail = $true
    } else {
      Say ("  [OK]   安装说明版本号 = v" + $ver)
    }
  }
}

Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue

Say ""
if ($fail) {
  Say "结论：X 检查未通过，不要上传这两个包。"
  $log | Out-File -Encoding utf8 $report
  Write-Host $log -join "`n"
  exit 1
} else {
  Say "结论：√ 两个包都干净，可以上传到 GitHub Release。"
  Say ""
  Say "下一步（在浏览器里手工做一次）："
  Say "  1. 打开 https://github.com/li198647/one-click-to-qq-chrome-extension/releases/new"
  Say "  2. Choose a tag 选 v$ver；Release title 填 v$ver"
  Say "  3. 把 dist\ 里这两个 zip 拖到页面下方的附件区，点 Publish release"
}

$log | Out-File -Encoding utf8 $report
Write-Host ($log -join "`n")
