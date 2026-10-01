#!/bin/bash
set -euo pipefail
MOBILE_ROOT="$(cd "$(dirname "$0")" && pwd)"
: "${JAVA_HOME:?请设置 JAVA_HOME 为 JDK 17 或 Android Studio 的 JBR 目录}"
: "${ANDROID_HOME:?请设置 ANDROID_HOME 为 Android SDK 目录}"
command -v go >/dev/null || { echo '需要 Go 1.27.1（项目 go.mod 固定版本）'; exit 1; }
[ -d "$ANDROID_HOME/platforms/android-35" ] || { echo '请安装 Android SDK platform 35'; exit 1; }
[ -d "$ANDROID_HOME/ndk/27.2.12479018" ] || { echo '请安装 NDK 27.2.12479018'; exit 1; }
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/27.2.12479018"
export PATH="$JAVA_HOME/bin:$PATH"
export GOBIN="$MOBILE_ROOT/tunnel/build/bin"
export PATH="$GOBIN:$PATH"
mkdir -p "$GOBIN" "$MOBILE_ROOT/android/app/libs"
cd "$MOBILE_ROOT/tunnel"
go mod download
go test ./...
MOBILE_VERSION='v0.0.0-20260908204917-8b95e45f8d3e'
go install "golang.org/x/mobile/cmd/gomobile@$MOBILE_VERSION"
go install "golang.org/x/mobile/cmd/gobind@$MOBILE_VERSION"
# NDK r27 defaults to 4 KB ELF alignment; ZIP alignment alone is insufficient.
gomobile bind -target android/arm64 -androidapi 26 -ldflags='-extldflags=-Wl,-z,max-page-size=16384,-z,common-page-size=16384' -javapkg com.charles.monitor.nativebridge -o "$MOBILE_ROOT/android/app/libs/monitor-tunnel.aar" .
cd "$MOBILE_ROOT/android"
./gradlew --no-daemon testDebugUnitTest assembleDebug
python3 "$MOBILE_ROOT/../scripts/check_android_native.py" "$MOBILE_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
"$ANDROID_HOME/build-tools/35.0.0/zipalign" -c -P 16 4 "$MOBILE_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
printf '\nAPK: %s\n' "$MOBILE_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
printf '这是原型调试包；编译通过不代表跨网连接已经通过真机验收。\n'
