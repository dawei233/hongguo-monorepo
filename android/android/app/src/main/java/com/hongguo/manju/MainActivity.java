package com.hongguo.manju;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

/**
 * 红果漫剧 NAS 版客户端：v28 纯 WebView 壳
 * 关键：用 WebView <video> 元素播（Chrome 内核自带 HEVC/H.264 解码，不依赖系统 MediaCodec）
 * ExoPlayer 严格 demuxer 不兼容部分 HEVC 容器；系统 MediaPlayer 在小米澎湃上对 HEVC 支持差
 * 后端 (server.py) 已经用 ffmpeg -decryption_key 解密 + 净化容器，前端直接播即可
 */
public class MainActivity extends Activity {

    private static final String PREFS = "hg_prefs";
    private static final String KEY_SERVER = "server_url";
    // 默认后端地址：改成本地 NAS 后端地址即可（首次打开也会在配置页让你填）。
    private static final String DEFAULT_SERVER = "http://192.168.1.100:8000";

    private WebView webView;
    private FrameLayout root;
    private SharedPreferences prefs;

    // v39: 接管 fullscreen — custom view 加到 root 最上层覆盖 WebView（视频画面正确显示）。
    // WebView 透明 surface 不可行（Chromium WebView 的 hardware layer 永远 opaque），
    // 所以 mFsContainer 底层方案作废。HTML ctrl-bar 全屏时被覆盖看不到，但用户按返回键退出全屏即可恢复。
    private boolean backHandling = false;
    private View mCustomView;
    private WebChromeClient.CustomViewCallback mCustomViewCallback;

    // JS → Java 桥
    private class Bridge {
        @JavascriptInterface
        public void saveServer(String url) {
            String u = (url == null ? "" : url.trim());
            if (!u.startsWith("http://") && !u.startsWith("https://")) {
                u = "http://" + u;
            }
            final String saved = u.replaceAll("/+$", "");
            prefs.edit().putString(KEY_SERVER, saved).apply();
            new Handler(Looper.getMainLooper()).post(() -> {
                Toast.makeText(MainActivity.this, "服务器已保存: " + saved, Toast.LENGTH_SHORT).show();
                loadServerPage(saved);
            });
        }

        @JavascriptInterface
        public String getServer() {
            return prefs.getString(KEY_SERVER, DEFAULT_SERVER);
        }

        @JavascriptInterface
        public void openConfig() {
            new Handler(Looper.getMainLooper()).post(() -> loadConfigPage());
        }

        // 检查设备是否支持 HEVC 硬解 — 告诉前端选源策略
        @JavascriptInterface
        public String getCaps() {
            boolean hevc = false;
            try {
                android.media.MediaCodecList list = new android.media.MediaCodecList(android.media.MediaCodecList.REGULAR_CODECS);
                for (android.media.MediaCodecInfo info : list.getCodecInfos()) {
                    if (!info.isEncoder() && info.getName().toLowerCase().contains("hevc")) {
                        for (String type : info.getSupportedTypes()) {
                            if (type.toLowerCase().contains("hevc") || type.contains("hvc1") || type.contains("hev1")) {
                                hevc = true;
                                break;
                            }
                        }
                        if (hevc) break;
                    }
                }
            } catch (Throwable t) {
                return "hevc=unknown;err=" + t.getMessage();
            }
            return "hevc=" + (hevc ? "yes" : "no");
        }

        // v29: 用 WebView <video> 元素播（Chrome 内核自带 HEVC/H.264 解码）。
        // 注意：不要设 controls=true — 那样浏览器原生控件会接管，且全屏时会被 WebChromeClient
        // 拉到独立 native View 上，导致我们的 .ctrl-bar 看不到。改用自定义 HTML ctrl-bar。
        @JavascriptInterface
        public void playExo(String url) {
            new Handler(Looper.getMainLooper()).post(() -> {
                if (webView == null) return;
                String escaped = url.replace("\\", "\\\\").replace("'", "\\'");
                String js = "(function(){"
                        + "  var v = document.getElementById('video');"
                        + "  if (!v) { console.error('no <video> element'); return; }"
                        + "  v.src = '" + escaped + "';"
                        + "  v.muted = false;"
                        + "  v.autoplay = true;"
                        + "  v.controls = false;"
                        + "  v.playsInline = true;"
                        + "  v.play().catch(function(e){ console.error('play err', e); });"
                        + "})()";
                webView.evaluateJavascript(js, null);
            });
        }

        @JavascriptInterface
        public void stopExo() {
            new Handler(Looper.getMainLooper()).post(() -> {
                if (webView == null) return;
                webView.evaluateJavascript(
                        "(function(){ var v = document.getElementById('video'); if (v) { v.pause(); v.removeAttribute('src'); v.load(); } })()",
                        null);
            });
        }

        @JavascriptInterface
        public boolean isExoActive() {
            // WebView video 元素是否在播放由前端状态决定，简单返回 false
            return false;
        }

        // v29: 全屏切换 — 前端调，Java 转屏 + 沉浸式
        // v38: 退出全屏时用 UNSPECIFIED（平板重力感应默认横屏，强制 PORTRAIT 会被重力转回）
        @JavascriptInterface
        public void toggleFs(final boolean fullscreen) {
            new Handler(Looper.getMainLooper()).post(() -> {
                if (fullscreen) {
                    setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
                    applyImmersive(true);
                } else {
                    setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
                    applyImmersive(false);
                }
            });
        }

        // v29: 隐藏状态栏 + 导航栏（沉浸式）。immersive sticky：用户上滑能看到，再次自动隐藏
        private void applyImmersive(boolean on) {
            int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_FULLSCREEN;
            if (on) flags |= View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
            getWindow().getDecorView().setSystemUiVisibility(flags);
        }
    }

    // v36: WebChromeClient 内部也要调 immersive（Bridge 的 applyImmersive 是 private，从这里复制一份）
    private void applyImmersiveExternal(boolean on) {
        int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN;
        if (on) flags |= View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
        getWindow().getDecorView().setSystemUiVisibility(flags);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);

        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);

        root = new FrameLayout(this);
        setContentView(root);

        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setAllowUniversalAccessFromFileURLs(true);
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowFileAccess(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUserAgentString("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36");

        webView.addJavascriptInterface(new Bridge(), "hgBridge");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return false;
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> request.grant(request.getResources()));
            }

            // v37: 真正接管 video fullscreen + 不覆盖 HTML ctrl-bar
            // v39: 真正接管 video fullscreen — custom view 加到 root 最上层覆盖 WebView。
            // 视频画面在 native SurfaceView 正确显示。HTML ctrl-bar 全屏时被覆盖看不到，
            // 用户按返回键退出全屏即可恢复。
            // 注：v37 的 mFsContainer 底层方案失败——Chromium WebView 的 hardware layer
            // 永远 opaque，即使 setBackgroundColor(TRANSPARENT) 也无法让底层 SurfaceView 透出。
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (mCustomView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                mCustomView = view;
                mCustomViewCallback = callback;
                ViewGroup parent = (ViewGroup) view.getParent();
                if (parent != null) parent.removeView(view);
                // 加到 root 最上层覆盖 WebView（MATCH_PARENT = 铺满全屏）
                root.addView(mCustomView, new FrameLayout.LayoutParams(
                        FrameLayout.LayoutParams.MATCH_PARENT,
                        FrameLayout.LayoutParams.MATCH_PARENT));
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
                applyImmersiveExternal(true);
                // 不通知前端 fs-mode（WebView 被覆盖，HTML ctrl-bar 反正看不到）
            }

            @Override
            public void onHideCustomView() {
                if (mCustomView == null) return;
                // 平板默认横屏（重力感应），UNSPECIFIED 让重力一步切回横屏，无中间 PORTRAIT 错乱
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
                applyImmersiveExternal(false);
                if (mCustomView.getParent() != null) {
                    ((ViewGroup) mCustomView.getParent()).removeView(mCustomView);
                }
                mCustomView = null;
                if (mCustomViewCallback != null) {
                    mCustomViewCallback.onCustomViewHidden();
                    mCustomViewCallback = null;
                }
            }
        });

        String server = prefs.getString(KEY_SERVER, "");
        if (server.isEmpty()) {
            loadConfigPage();
        } else {
            loadServerPage(server);
        }
    }

    private void showPlayerError(String msg) {
        // v28: 不再显示错误（WebView video 自带错误处理）
    }

    private void loadServerPage(String url) {
        webView.loadUrl(url);
    }

    private void loadConfigPage() {
        String html = "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                + "<style>"
                + "body{background:#0F1115;color:#fff;font:16px/1.6 -apple-system,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:20px}"
                + ".box{max-width:420px;width:100%}"
                + "h1{font-size:22px;margin:0 0 6px;text-align:center}"
                + ".sub{color:#8b94a7;text-align:center;margin-bottom:24px;font-size:13px}"
                + "input{width:100%;box-sizing:border-box;background:#1a1f2a;border:1px solid #333;color:#fff;border-radius:10px;padding:14px 16px;font-size:16px;margin-bottom:12px}"
                + "button{width:100%;background:#ff4757;color:#fff;border:none;border-radius:10px;padding:14px;font-size:16px;font-weight:600;cursor:pointer}"
                + ".hint{color:#6b7488;font-size:12px;margin-top:14px;line-height:1.7}"
                + "</style></head><body>"
                + "<div class='box'>"
                + "<h1>红果漫剧 · NAS 版</h1>"
                + "<p class='sub'>填写 NAS 后端服务地址</p>"
                + "<input id='url' type='url' value='' placeholder='例如 192.168.1.100:8000'>"
                + "<button onclick='save()'>连接服务器</button>"
                + "<p class='hint'>提示：地址是 NAS 上红果后端的访问地址（含端口，如 192.168.1.100:8000）。</p>"
                + "</div>"
                + "<script>"
                + "window.onload = function(){ document.getElementById('url').value = hgBridge.getServer(); };"
                + "function save(){ var v = document.getElementById('url').value.trim(); if(!v){alert('请输入地址');return;} hgBridge.saveServer(v); }"
                + "</script></body></html>";
        webView.loadDataWithBaseURL("file:///android_asset/", html, "text/html", "UTF-8", null);
    }

    @Override
    public void onBackPressed() {
        // v36: 先检查 native fullscreen custom view（如果 video 在 native fullscreen 中，优先退出）
        if (mCustomView != null) {
            webView.getWebChromeClient().onHideCustomView();
            return;
        }
        if (backHandling) return;
        backHandling = true;
        webView.evaluateJavascript(
                "(function(){" +
                "  if (document.body.classList.contains('fs-mode')) { toggleFullscreen(); return 'handled'; }" +
                "  var pv = document.getElementById('player-view');" +
                "  if (pv && pv.classList.contains('show')) { showSearch(); return 'handled'; }" +
                "  return 'exit';" +
                "})()",
                value -> {
                    backHandling = false;
                    if (value != null && value.contains("exit")) {
                        runOnUiThread(MainActivity.this::finish);
                    }
                });
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) webView.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
