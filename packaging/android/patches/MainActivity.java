package xyz.xykong.linknote;

import android.content.Intent;
import android.os.Bundle;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

import org.json.JSONObject;

import java.util.Locale;

/**
 * LinkNote Android shell.
 *
 * Receives text/links shared from other Android apps (ACTION_SEND / text/plain) and hands them
 * to the web layer through two complementary channels:
 *
 *   1. Push: once the WebView page has finished loading, the shared text is injected as
 *      window.__LINKNOTE_SHARED_TEXT__ and a `linknote:share` CustomEvent is dispatched with
 *      detail { text }. This covers cold start (share -> app launch) and warm start
 *      (app in background -> onNewIntent).
 *
 *   2. Pull: window.LinkNoteNative.getSharedText() returns a Promise<string|null> backed by a
 *      JavascriptInterface. It is always available, so the web layer can read the latest share
 *      at any moment of its own boot sequence, even if it attached its event listener late.
 */
public class MainActivity extends BridgeActivity {

    private static final String TAG = "LinkNoteShare";
    private static final String JS_INTERFACE_NAME = "LinkNoteAndroid";

    /** Latest text received from an Android share intent (kept until replaced by the next share). */
    private volatile String sharedText = null;
    /** Last text pushed into the current page, used to avoid duplicate pushes. */
    private volatile String lastInjectedText = null;
    /** True once the Capacitor WebView fired onPageFinished for the main page. */
    private volatile boolean pageLoaded = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        if (getBridge() == null || getBridge().getWebView() == null) {
            Log.w(TAG, "Bridge/WebView unavailable; share text cannot be delivered");
            return;
        }

        WebView webView = getBridge().getWebView();
        // Always-available pull channel for the web layer.
        webView.addJavascriptInterface(new LinkNoteNativeBridge(), JS_INTERFACE_NAME);
        // Extend Capacitor's WebViewClient so we keep all bridge behaviour and learn when the page
        // is ready, then push whatever share text arrived while it was still loading.
        webView.setWebViewClient(new ShareAwareWebViewClient(getBridge()));

        if (pageLoaded) {
            injectSharedText(webView);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);

        // BridgeActivity.load() invokes onNewIntent(getIntent()) while the bridge is still being
        // created, so the pending text is queued here and pushed once the page finishes loading.
        String text = extractSharedText(intent);
        if (text == null) {
            return;
        }
        sharedText = text;
        lastInjectedText = null;
        Log.i(TAG, "Received shared text (" + text.length() + " chars)");

        if (getBridge() != null && getBridge().getWebView() != null && pageLoaded) {
            injectSharedText(getBridge().getWebView());
        }
    }

    /** Returns the shared text, or null when the intent is not a text share. */
    private String extractSharedText(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            return null;
        }
        String type = intent.getType();
        if (type != null && !type.startsWith("text/")) {
            return null;
        }
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null || text.trim().isEmpty()) {
            // Some apps only put the link in EXTRA_SUBJECT; accept it when it looks like a link.
            String subject = intent.getStringExtra(Intent.EXTRA_SUBJECT);
            if (subject != null && looksLikeLink(subject)) {
                text = subject;
            }
        }
        if (text == null || text.trim().isEmpty()) {
            return null;
        }
        return text;
    }

    private static boolean looksLikeLink(String value) {
        String v = value.trim().toLowerCase(Locale.ROOT);
        return v.contains("http://") || v.contains("https://") || v.startsWith("www.");
    }

    /** Pushes the current share text (and the JS shim) into the page. Must run on the UI thread. */
    private void injectSharedText(WebView webView) {
        final String text = sharedText;
        if (text == null || text.equals(lastInjectedText)) {
            return;
        }
        lastInjectedText = text;
        String js = "(function(){try{"
                + "var t=" + JSONObject.quote(text) + ";"
                + "window.__LINKNOTE_SHARED_TEXT__=t;"
                + "window.__LINKNOTE_SHARED_AT__=Date.now();"
                + "if(!window.LinkNoteNative){window.LinkNoteNative={};}"
                + "window.LinkNoteNative.getSharedText=function(){return new Promise(function(resolve){"
                + "try{resolve(window." + JS_INTERFACE_NAME + "?window." + JS_INTERFACE_NAME + ".getSharedText():t);}"
                + "catch(e){resolve(t);}});};"
                + "try{window.dispatchEvent(new CustomEvent('linknote:share',{detail:{text:t}}));}catch(e){}"
                + "}catch(e){}})();";
        webView.evaluateJavascript(js, null);
    }

    /** Defines window.LinkNoteNative on a page even when no share text has been received yet. */
    private void injectNativeShim(WebView webView) {
        String js = "(function(){try{"
                + "if(!window.LinkNoteNative){window.LinkNoteNative={};}"
                + "if(!window.LinkNoteNative.getSharedText){"
                + "window.LinkNoteNative.getSharedText=function(){return new Promise(function(resolve){"
                + "try{resolve(window." + JS_INTERFACE_NAME + "?window." + JS_INTERFACE_NAME + ".getSharedText():(window.__LINKNOTE_SHARED_TEXT__||null));}"
                + "catch(e){resolve(window.__LINKNOTE_SHARED_TEXT__||null);}});};}"
                + "}catch(e){}})();";
        webView.evaluateJavascript(js, null);
    }

    /** Keeps Capacitor's WebViewClient behaviour and hooks page-finished to push share text. */
    private final class ShareAwareWebViewClient extends BridgeWebViewClient {
        ShareAwareWebViewClient(com.getcapacitor.Bridge bridge) {
            super(bridge);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            pageLoaded = true;
            injectNativeShim(view);
            // 只在该次分享尚未注入过时注入：页面重载/重建不会重复 dispatch 同一个分享，
            // 前端若错过事件仍可通过 window.LinkNoteNative.getSharedText() 拉取。
            injectSharedText(view);
        }
    }

    /** Synchronous bridge used by window.LinkNoteNative.getSharedText(). */
    private final class LinkNoteNativeBridge {
        @JavascriptInterface
        public String getSharedText() {
            return sharedText;
        }

        @JavascriptInterface
        public void clearSharedText() {
            sharedText = null;
            lastInjectedText = null;
        }
    }
}
