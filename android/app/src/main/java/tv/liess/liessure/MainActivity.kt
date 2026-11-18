package tv.liess.liessure

import android.app.Activity
import android.app.SearchManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.security.KeyChain
import android.view.WindowInsets
import android.webkit.ClientCertRequest
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import kotlin.concurrent.thread

/**
 * A thin shell around the Liessure web app.
 *
 * What it adds over a browser tab:
 *  - it can be the handler for reddit.com links, so tapping one lands here;
 *  - cards open in the app that owns them (Immich, Jellyfin, a browser), and
 *    song cards can launch Symfonium directly;
 *  - it answers the gatekeeper's mTLS challenge with the client certificate
 *    installed on the phone, so it works off the home network too;
 *  - with no network it falls back to the WebView's cache, so the page (and
 *    the cards it saved last time) still opens.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private val home: Uri by lazy { Uri.parse(BuildConfig.LIESSURE_URL) }

    /** Hosts that stay inside the WebView; everything else goes to its own app. */
    private val inAppHosts: Set<String> by lazy {
        setOf(home.host.orEmpty(), "sso.liess.tv", "auth.liess.tv", "gatekeeper.liess.tv", "gatekeeper-lan.liess.tv")
    }

    private val prefs by lazy { getSharedPreferences("liessure", Context.MODE_PRIVATE) }

    /** Hosts the feed asked to show in place (openInApp), e.g. Jellyfin; cleared on the next feed load. */
    private val visiting = mutableSetOf<String>()

    /** Set while a load is being retried from cache after a network error. */
    private var fromCache = false

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
            mediaPlaybackRequiresUserGesture = false
            userAgentString = "$userAgentString LiessureAndroid/${BuildConfig.VERSION_NAME}"
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true) // Invidious embeds
        web.addJavascriptInterface(Bridge(), "LiessureAndroid")
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
        fromCache = false
        visiting.clear()
        web.settings.cacheMode = WebSettings.LOAD_DEFAULT
        val fromReddit = intent?.data?.host?.contains("redd") == true
        val url = if (fromReddit) home.buildUpon().appendQueryParameter("from", "reddit").build() else home
        web.loadUrl(url.toString())
    }

    private fun isHome(url: String?): Boolean = url != null && Uri.parse(url).host == home.host

    /** Open a link in whichever app claims it, never back into Liessure itself. */
    private fun openExternal(raw: String) {
        val uri = Uri.parse(raw)
        val intent = if (uri.scheme == "intent") {
            runCatching { Intent.parseUri(raw, Intent.URI_INTENT_SCHEME) }.getOrNull() ?: return
        } else {
            // Web links go to whichever app claims them (YouTube, Immich, a browser). Custom
            // schemes like argosy:// are handled by apps whose filters lack BROWSABLE.
            Intent(Intent.ACTION_VIEW, uri).apply {
                if (uri.scheme == "http" || uri.scheme == "https") addCategory(Intent.CATEGORY_BROWSABLE)
            }
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

    /**
     * Symfonium has an API for this (docs.symfonium.app, "Symfonium API"): a broadcast that plays a
     * song found by title, artist and album. Other players get Android's play-from-search intent.
     */
    private fun playSong(pkg: String, title: String, artist: String, album: String) {
        if (pkg == SYMFONIUM) {
            sendBroadcast(
                Intent("app.symfonium.api.MEDIA_START")
                    .setComponent(ComponentName(SYMFONIUM, "app.symfonium.remote.api.ApiReceiver"))
                    .putExtra("MEDIA_TYPE", "song")
                    .putExtra("NAME", title)
                    .putExtra("ARTIST", artist)
                    .putExtra("ALBUM", album),
            )
            launchApp(pkg)
            return
        }
        val search = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
            .setPackage(pkg)
            .putExtra(SearchManager.QUERY, "$title $artist".trim())
            .putExtra(MediaStore.EXTRA_MEDIA_FOCUS, MediaStore.Audio.Media.ENTRY_CONTENT_TYPE)
            .putExtra(MediaStore.EXTRA_MEDIA_TITLE, title)
            .putExtra(MediaStore.EXTRA_MEDIA_ARTIST, artist)
            .putExtra(MediaStore.EXTRA_MEDIA_ALBUM, album)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(search)
        } catch (e: ActivityNotFoundException) {
            launchApp(pkg)
        }
    }

    /** Exposed to the page as window.LiessureAndroid. Only honoured for our own origin. */
    inner class Bridge {
        @JavascriptInterface
        fun openUrl(url: String) = runOnUiThread { if (isHome(web.url)) openExternal(url) }

        @JavascriptInterface
        fun launchApp(pkg: String) = runOnUiThread {
            if (isHome(web.url) && pkg.matches(Regex("[A-Za-z0-9_.]+"))) this@MainActivity.launchApp(pkg)
        }

        @JavascriptInterface
        fun retry() = runOnUiThread { loadFor(intent) }

        /** Show a page (Jellyfin's item page) inside the app; Back returns to the feed. */
        @JavascriptInterface
        fun openInApp(url: String) = runOnUiThread {
            val uri = Uri.parse(url)
            if (isHome(web.url) && uri.scheme == "https" && !uri.host.isNullOrEmpty()) {
                visiting.add(uri.host!!)
                web.loadUrl(url)
            }
        }

        /** Start one song in the music app, then bring the app up. */
        @JavascriptInterface
        fun playSong(pkg: String, title: String, artist: String, album: String) = runOnUiThread {
            if (isHome(web.url) && pkg.matches(Regex("[A-Za-z0-9_.]+"))) this@MainActivity.playSong(pkg, title, artist, album)
        }
    }

    inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            if (!request.isForMainFrame) return false // inline players (iframes) stay put
            if ((uri.scheme == "https" || uri.scheme == "http") && (uri.host in inAppHosts || uri.host in visiting)) return false
            openExternal(uri.toString())
            return true
        }

        override fun onPageFinished(view: WebView, url: String?) {
            // The cached page is up; let its own requests try the network again.
            view.settings.cacheMode = WebSettings.LOAD_DEFAULT
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            // Offline (normally the service worker answers; this covers before it's installed):
            // open the last copy of the page once, and it shows the cards it saved.
            if (!fromCache && isHome(request.url.toString()) && error.errorCode in OFFLINE_ERRORS) {
                fromCache = true
                view.settings.cacheMode = WebSettings.LOAD_CACHE_ONLY
                view.loadUrl(request.url.toString())
                return
            }
            val html = """
                <html><body style="background:#14161a;color:#eceae6;font-family:sans-serif;
                  display:flex;flex-direction:column;align-items:center;justify-content:center;height:90vh">
                <p>Couldn't reach Liessure.</p><p style="color:#9aa0a8;font-size:14px">${error.description}</p>
                <button style="font-size:16px;padding:10px 18px;border-radius:8px;border:0;background:#7cc4ad"
                  onclick="LiessureAndroid.retry()">Try again</button></body></html>
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

    private companion object {
        const val SYMFONIUM = "app.symfonik.music.player"
        val OFFLINE_ERRORS = setOf(
            WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_TIMEOUT,
        )
    }
}
