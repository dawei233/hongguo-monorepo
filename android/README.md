# 安卓平板版前端（hongguo-android）

> 安卓 App 壳（WebView + JS Bridge），加载 NAS 后端的 HTML/CSS/JS

---

## ⚠️ 关键架构决策（v34+）

**APK 内的 `assets/index.html` 是冗余的**——所有 UI 由 NAS 后端提供。

```java
// MainActivity.java
public void loadServerPage(String url) {
    webView.loadUrl(url);   // 加载 NAS URL，不读 assets
}
```

**修改前端不需要重新构建 APK**。流程：
1. 改 `nas-backend/static/index.html`
2. NAS docker compose build + up
3. 平板刷新即可

**只有改 Java 代码时才需要重新构建 APK**。

---

## 目录结构

```
android/
├── android/                              # Android 项目根目录
│   ├── app/
│   │   ├── build.gradle                  # 应用模块 Gradle
│   │   └── src/main/
│   │       ├── AndroidManifest.xml       # 网络权限 + 全屏主题
│   │       ├── assets/
│   │       │   └── index.html            # 冗余 HTML（实际从 NAS 加载）
│   │       ├── java/com/hongguo/manju/
│   │       │   └── MainActivity.java     # WebView 壳 + JS Bridge
│   │       └── res/                      # 图标 + 资源文件
│   ├── build.gradle                      # 项目级 Gradle
│   ├── settings.gradle
│   ├── gradle.properties
│   └── gradle/                           # Gradle wrapper
├── .github/workflows/
│   └── build.yml                         # GitHub Actions 自动构建 APK
├── .gitignore                            # 排除 build/、.gradle/ 等
├── signing/
│   ├── key.pem                           # 上传密钥
│   ├── cert.pem
│   ├── hongguo.jks                       # APK 签名密钥库
│   └── 密码备份.txt                       # ⚠️ 不要泄露
├── 发布版/
│   ├── 红果漫剧_安卓版.apk               # 编译产物（gitignore）
│   ├── edge_profile/                     # WebView 数据（gitignore）
│   └── webview_data/                     # WebView 数据（gitignore）
└── README.md                             # 本文件
```

---

## MainActivity 关键代码

### WebView 配置
```java
WebSettings settings = webView.getSettings();
settings.setJavaScriptEnabled(true);
settings.setMediaPlaybackRequiresUserGesture(false);
settings.setAllowFileAccess(true);
settings.setDomStorageEnabled(true);
settings.setDatabaseEnabled(true);
```

### 加载 NAS URL
```java
public void loadServerPage(String url) {
    webView.loadUrl(url);   // url = "http://<NAS_IP>:8000"
}
```

### 全屏接管（v36+）
```java
@Override
public void onShowCustomView(View view, WebChromeClient.CustomViewCallback callback) {
    // 把 WebView 的 fullscreen custom view（SurfaceView））加到 mFsContainer
    // + 转横屏 + 沉浸式
    mFsContainer.addView(view);
    setRequestedOrientation(SCREEN_ORIENTATION_LANDSCAPE);
    applyImmersive(true);
}
```

### JS Bridge
```java
// hgBridge.playExo(url) — ExoPlayer 备用（v22-v34 用，现已切 WebView <video>）
webView.addJavascriptInterface(new Object() {
    @JavascriptInterface
    public void playExo(String url) {
        // 创建 ExoPlayer 播放
    }
}, "hgBridge");
```

---

## GitHub Actions 自动构建

`.github/workflows/build.yml` 触发：
- `on: push` 主分支
- `on: pull_request`
- `on: workflow_dispatch`（手动触发）

构建步骤：
1. `actions/checkout@v4`
2. 安装 JDK 17 + Android SDK
3. 下载 Gradle 8.9
4. `gradle assembleDebug`
5. 上传 APK 到 Artifacts（`hongguo-manju-apk`）

下载：在 GitHub 仓库 → Actions → 选择 run → 底部 Artifacts → 下载 zip → 解压得到 APK。

---

## 本地构建（可选）

```bash
# 需要 JDK 17 + Android SDK
cd android/android
gradle assembleDebug
# 产物: app/build/outputs/apk/debug/app-debug.apk

# 安装到平板
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

---

## 修改 Java 后重新发版的流程

```bash
# 1. 改 Java
$EDITOR android/android/app/src/main/java/com/hongguo/manju/MainActivity.java

# 2. 提交 + 推送
cd android
git add android/android/app/src/main/java/com/hongguo/manju/MainActivity.java
git commit -m "vXX: <修改内容>"
git push origin main

# 3. GitHub Actions 自动构建
# 访问 https://github.com/<你的仓库>/actions
# 找到最新 run → 下载 Artifacts

# 4. 平板安装新 APK
# 设置 → 应用 → 红果漫剧 → 卸载 → 安装新 APK
# 或 adb install -r app-debug.apk
```

---

## 调试

```bash
# USB 调试
adb devices
adb logcat | grep -i chromium

# WebView 远程调试
# Chrome 浏览器 → chrome://inspect/#devices
# 平板允许 USB 调试后可见
```

---

## 关联文档

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — 安卓在整体架构中的角色
- [`../docs/CHANGELOG.md`](../docs/CHANGELOG.md) — v22-v42 安卓端变更
- [`../docs/DEPLOY.md`](../docs/DEPLOY.md) — APK 构建 + 安装
- [`../README.md`](../README.md) — monorepo 总入口