package tv.liess.detour

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.security.KeyChain
import android.view.WindowInsets
import android.webkit.ClientCertRequest
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import kotlin.concurrent.thread

/**
 * A thin shell around the Detour web app.
 *
 * What it adds over a browser tab:
 *  - it can be the handler for reddit.com links, so tapping one lands here;
 *  - cards open in the app that owns them (Immich, Jellyfin, a browser), and
 *    song cards can launch Symfonium directly;
 *  - it answers the gatekeeper's mTLS challenge with the client certificate
 *    installed on the phone, so it works off the home network too.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private val home: Uri by lazy { Uri.parse(BuildConfig.DETOUR_URL) }

    /** Hosts that stay inside the WebView; everything else goes to its own app. */
    private val inAppHosts: Set<String> by lazy {
        setOf(home.host.orEmpty(), "sso.liess.tv", "auth.liess.tv", "gatekeeper.liess.tv", "gatekeeper-lan.liess.tv")
    }

    private val prefs by lazy { getSharedPreferences("detour", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)

        // targetSdk 35 draws edge to edge; keep the page clear of the system bars.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            web.setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                WindowInsets.CONSUMED
            }
        }

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            userAgentString = "$userAgentString DetourAndroid/${BuildConfig.VERSION_NAME}"
        }
        web.addJavascriptInterface(Bridge(), "DetourAndroid")
        web.webChromeClient = WebChromeClient()
        web.webViewClient = Client()

        if (savedInstanceState != null) web.restoreState(savedInstanceState) else loadFor(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        loadFor(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Activity back handling; fine for a single-screen shell")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    private fun loadFor(intent: Intent?) {
        val fromReddit = intent?.data?.host?.contains("redd") == true
        val url = if (fromReddit) home.buildUpon().appendQueryParameter("from", "reddit").build() else home
        web.loadUrl(url.toString())
    }

    private fun isHome(url: String?): Boolean = url != null && Uri.parse(url).host == home.host

    /** Open a link in whichever app claims it, never back into Detour itself. */
    private fun openExternal(raw: String) {
        val uri = Uri.parse(raw)
        val intent = if (uri.scheme == "intent") {
            runCatching { Intent.parseUri(raw, Intent.URI_INTENT_SCHEME) }.getOrNull() ?: return
        } else {
            Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val target = if (intent.resolveActivity(packageManager)?.packageName == packageName) {
            // e.g. a reddit link while we're the reddit handler: offer anything but us.
            Intent.createChooser(intent, null)
                .putExtra(Intent.EXTRA_EXCLUDE_COMPONENTS, arrayOf(ComponentName(this, MainActivity::class.java)))
        } else intent
        try {
            startActivity(target)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "Nothing on this phone opens that link", Toast.LENGTH_SHORT).show()
        }
    }

    private fun launchApp(pkg: String) {
        val launch = packageManager.getLaunchIntentForPackage(pkg)
        if (launch != null) {
            startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } else {
            openExternal("market://details?id=$pkg")
        }
    }

    /** Exposed to the page as window.DetourAndroid. Only honoured for our own origin. */
    inner class Bridge {
        @JavascriptInterface
        fun openUrl(url: String) = runOnUiThread { if (isHome(web.url)) openExternal(url) }

        @JavascriptInterface
        fun launchApp(pkg: String) = runOnUiThread {
            if (isHome(web.url) && pkg.matches(Regex("[A-Za-z0-9_.]+"))) this@MainActivity.launchApp(pkg)
        }

        @JavascriptInterface
        fun retry() = runOnUiThread { loadFor(intent) }
    }

    inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            if ((uri.scheme == "https" || uri.scheme == "http") && uri.host in inAppHosts) return false
            openExternal(uri.toString())
            return true
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            val html = """
                <html><body style="background:#14161a;color:#eceae6;font-family:sans-serif;
                  display:flex;flex-direction:column;align-items:center;justify-content:center;height:90vh">
                <p>Couldn't reach Detour.</p><p style="color:#9aa0a8;font-size:14px">${error.description}</p>
                <button style="font-size:16px;padding:10px 18px;border-radius:8px;border:0;background:#7cc4ad"
                  onclick="DetourAndroid.retry()">Try again</button></body></html>
            """.trimIndent()
            view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        }

        override fun onReceivedClientCertRequest(view: WebView, request: ClientCertRequest) {
            val saved = prefs.getString("cert_alias", null)
            if (saved != null) {
                thread {
                    if (!answer(request, saved)) {
                        prefs.edit().remove("cert_alias").apply()
                        runOnUiThread { chooseCert(request) }
                    }
                }
            } else {
                chooseCert(request)
            }
        }
    }

    private fun chooseCert(request: ClientCertRequest) {
        KeyChain.choosePrivateKeyAlias(
            this,
            { alias ->
                if (alias == null) {
                    request.cancel()
                } else if (answer(request, alias)) {
                    prefs.edit().putString("cert_alias", alias).apply()
                } else {
                    request.cancel()
                }
            },
            request.keyTypes, request.principals, request.host, request.port, null,
        )
    }

    /** Runs off the main thread: KeyChain lookups block. */
    private fun answer(request: ClientCertRequest, alias: String): Boolean = runCatching {
        val key = KeyChain.getPrivateKey(this, alias) ?: return false
        val chain = KeyChain.getCertificateChain(this, alias) ?: return false
        request.proceed(key, chain)
        true
    }.getOrDefault(false)
}
