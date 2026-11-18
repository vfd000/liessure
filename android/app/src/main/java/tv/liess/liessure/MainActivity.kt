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
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Base64
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.FileProvider
import java.security.MessageDigest
import java.security.SecureRandom
import java.io.File
import android.security.KeyChain
import android.view.WindowInsets
import android.webkit.ClientCertRequest
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import java.util.Locale
import org.json.JSONObject
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

    /** Speaks phrase cards; created on first use, released in onDestroy. */
    private var tts: TextToSpeech? = null
    private var ttsReady = false

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

    override fun onDestroy() {
        tts?.shutdown()
        super.onDestroy()
    }

    /** The text-to-speech engine, made on first use; [then] runs once it's ready, [failed] if it can't start. */
    private fun withTts(then: (TextToSpeech) -> Unit, failed: () -> Unit) {
        val engine = tts
        if (ttsReady && engine != null) {
            then(engine)
            return
        }
        tts = TextToSpeech(this) { status ->
            runOnUiThread {
                val made = tts
                if (status == TextToSpeech.SUCCESS && made != null) {
                    ttsReady = true
                    made.setOnUtteranceProgressListener(Progress())
                    then(made)
                } else {
                    failed()
                }
            }
        }
    }

    /** Tells the page an article finished (or failed): window.liessureSpoken(id, ok). */
    inner class Progress : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {}
        override fun onDone(utteranceId: String?) = finished(utteranceId, true)
        @Deprecated("Deprecated in Java")
        override fun onError(utteranceId: String?) = finished(utteranceId, false)
        override fun onError(utteranceId: String?, errorCode: Int) = finished(utteranceId, false)

        private fun finished(utteranceId: String?, ok: Boolean) {
            val id = utteranceId?.takeIf { it.startsWith("long:") }?.removePrefix("long:") ?: return
            spoken(id, ok)
        }
    }

    private fun spoken(id: String, ok: Boolean) = runOnUiThread {
        web.evaluateJavascript("window.liessureSpoken && window.liessureSpoken(${JSONObject.quote(id)}, $ok)", null)
    }

    /** Read an article aloud in sentence-bounded pieces; the last piece's id tells the page when it's done. */
    private fun speakLong(text: String, lang: String, id: String) {
        withTts({ engine ->
            val result = engine.setLanguage(Locale.forLanguageTag(lang.ifEmpty { "en" }))
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                Toast.makeText(this, "No voice for that language on this phone", Toast.LENGTH_SHORT).show()
                spoken(id, false)
                return@withTts
            }
            engine.setSpeechRate(1.0f)
            engine.setOnUtteranceProgressListener(Progress()) // the phrase speaker may have made the engine
            val max = minOf(3000, TextToSpeech.getMaxSpeechInputLength() - 1)
            val pieces = mutableListOf<String>()
            val current = StringBuilder()
            for (sentence in text.split(Regex("(?<=[.!?\n])\\s+"))) {
                if (current.length + sentence.length + 1 > max && current.isNotEmpty()) {
                    pieces.add(current.toString())
                    current.clear()
                }
                current.append(sentence.take(max)).append(' ')
            }
            if (current.isNotBlank()) pieces.add(current.toString())
            if (pieces.isEmpty()) {
                spoken(id, true)
                return@withTts
            }
            pieces.forEachIndexed { i, piece ->
                val utterance = if (i == pieces.lastIndex) "long:$id" else "part:$id:$i"
                engine.speak(piece, if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, utterance)
            }
        }, { spoken(id, false) })
    }

    /** Say a phrase in its language; if the phone has no voice for it, open the fallback (Google Translate). */
    private fun speak(text: String, lang: String, fallback: String) {
        val say = say@{
            val engine = tts ?: return@say
            val result = engine.setLanguage(Locale.forLanguageTag(lang))
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                Toast.makeText(this, "No voice for that language on this phone", Toast.LENGTH_SHORT).show()
                if (fallback.startsWith("https://")) openExternal(fallback)
                return@say
            }
            engine.setSpeechRate(0.85f)
            engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "liessure-phrase")
        }
        if (ttsReady) {
            say()
            return
        }
        tts = TextToSpeech(this) { status ->
            runOnUiThread {
                if (status == TextToSpeech.SUCCESS) {
                    ttsReady = true
                    say()
                } else if (fallback.startsWith("https://")) {
                    openExternal(fallback)
                }
            }
        }
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
        val data = intent?.data
        if (data?.scheme == "liessure" && data.host == "signed-in") {
            finishSignIn(data)
            return
        }
        fromCache = false
        visiting.clear()
        web.settings.cacheMode = WebSettings.LOAD_DEFAULT
        // A Jellyfin link caught from another app: that page here (https only), Back leaves the app.
        if (data != null && data.host == BuildConfig.JELLYFIN_HOST && (data.scheme == "https" || data.scheme == "http")) {
            visiting.add(BuildConfig.JELLYFIN_HOST)
            web.loadUrl(data.buildUpon().scheme("https").build().toString())
            return
        }
        val fromReddit = intent?.data?.host?.contains("redd") == true
        val url = if (fromReddit) home.buildUpon().appendQueryParameter("from", "reddit").build() else home
        web.loadUrl(url.toString())
    }

    private fun isHome(url: String?): Boolean = url != null && Uri.parse(url).host == home.host

    private var signInStarted = 0L

    /**
     * Sign in in a Chrome Custom Tab: the WebView can't use the phone's passkeys, Chrome can. The app keeps a
     * random verifier and sends only its hash; Liessure hands the session back as a one-time ticket via
     * liessure://signed-in, which only that verifier can redeem.
     */
    private fun startSignIn() {
        val now = System.currentTimeMillis()
        if (now - signInStarted < 3000) return // the redirect can arrive twice; one tab is enough
        signInStarted = now
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val flags = Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP
        val verifier = Base64.encodeToString(bytes, flags)
        val challenge = Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray()), flags)
        prefs.edit().putString("signin_verifier", verifier).apply()
        val url = home.buildUpon().path("/auth/login").clearQuery().appendQueryParameter("app", challenge).build()
        val html = """
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="background:#14161a;color:#eceae6;font-family:sans-serif;padding:24px;display:flex;
              flex-direction:column;align-items:center;justify-content:center;min-height:85vh;text-align:center">
            <p style="font-size:18px">Signing in with your browser…</p>
            <p style="color:#9aa0a8;font-size:14px">Finish there (passkey or password); you'll come straight back.</p>
            <button style="font-size:16px;padding:10px 18px;border-radius:8px;border:0;background:#7cc4ad"
              onclick="LiessureAndroid.signIn()">Open sign-in again</button></body></html>
        """.trimIndent()
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
        try {
            CustomTabsIntent.Builder().build().launchUrl(this, url)
        } catch (e: ActivityNotFoundException) {
            openExternal(url.toString())
        }
    }

    private fun finishSignIn(data: Uri) {
        val ticket = data.getQueryParameter("ticket").orEmpty()
        val verifier = prefs.getString("signin_verifier", null).orEmpty()
        prefs.edit().remove("signin_verifier").apply()
        signInStarted = 0L
        setIntent(Intent(Intent.ACTION_MAIN)) // a retry later reloads the feed, not this used-up link
        web.loadUrl(home.buildUpon().path("/auth/ticket").clearQuery()
            .appendQueryParameter("ticket", ticket).appendQueryParameter("verifier", verifier).build().toString())
    }

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
            // An intent: link with a fallback (e.g. Google Translate not installed): open the fallback here.
            val fallback = if (uri.scheme == "intent") intent.getStringExtra("browser_fallback_url").orEmpty() else ""
            val fb = Uri.parse(fallback)
            if (fb.scheme == "https" && !fb.host.isNullOrEmpty()) {
                visiting.add(fb.host!!)
                web.loadUrl(fallback)
                return
            }
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

    /** web.url must be read on the UI thread; JavascriptInterface methods run on another one. */
    private fun isHomeOnUi(): Boolean {
        val latch = java.util.concurrent.CountDownLatch(1)
        var ok = false
        runOnUiThread { ok = isHome(web.url); latch.countDown() }
        latch.await(2, java.util.concurrent.TimeUnit.SECONDS)
        return ok
    }

    private fun shareImage(bytes: ByteArray, mime: String, text: String) {
        val dir = File(cacheDir, "shared").apply { mkdirs() }
        val file = File(dir, if (mime == "image/png") "photo.png" else "photo.jpg")
        file.writeBytes(bytes)
        val uri = FileProvider.getUriForFile(this, "tv.liess.liessure.files", file)
        val send = Intent(Intent.ACTION_SEND)
            .setType(mime)
            .putExtra(Intent.EXTRA_STREAM, uri)
            .putExtra(Intent.EXTRA_TEXT, text)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        val claude = Intent(send).setPackage(CLAUDE)
        val target = if (claude.resolveActivity(packageManager) != null) claude else Intent.createChooser(send, "Ask about this photo")
        try {
            startActivity(target.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "Nothing on this phone takes a shared photo", Toast.LENGTH_SHORT).show()
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

        @JavascriptInterface
        fun signIn() = runOnUiThread { signInStarted = 0L; startSignIn() }

        /** Pick a .p12 file and hand it to Android's certificate installer (it asks for the password). */
        @JavascriptInterface
        fun installCert() = runOnUiThread { pickCert() }

        /** Forget the remembered certificate so the next request asks again. */
        @JavascriptInterface
        fun chooseCertAgain() = runOnUiThread { forgetCert() }

        /** Show a page (Jellyfin's item page) inside the app; Back returns to the feed. */
        @JavascriptInterface
        fun openInApp(url: String) = runOnUiThread {
            val uri = Uri.parse(url)
            if (isHome(web.url) && uri.scheme == "https" && !uri.host.isNullOrEmpty()) {
                visiting.add(uri.host!!)
                web.loadUrl(url)
            }
        }

        /** Ask Claude about a photo: the image (base64 from the page) shared into the Claude app. */
        @JavascriptInterface
        fun shareImage(base64: String, mime: String, text: String) {
            if (!isHomeOnUi() || base64.length > 20_000_000 || !mime.startsWith("image/")) return
            val bytes = runCatching { Base64.decode(base64, Base64.DEFAULT) }.getOrNull() ?: return
            runOnUiThread { this@MainActivity.shareImage(bytes, mime, text) }
        }

        /** Say a phrase card aloud with the phone's text-to-speech. */
        @JavascriptInterface
        fun speak(text: String, lang: String, fallback: String) = runOnUiThread {
            if (isHome(web.url) && text.length <= 500) this@MainActivity.speak(text, lang, fallback)
        }

        /** Read an article aloud (Listen, and the feed's podcast mode); window.liessureSpoken(id, ok) when done. */
        @JavascriptInterface
        fun speakLong(text: String, lang: String, id: String) = runOnUiThread {
            if (isHome(web.url) && text.length <= 100_000 && id.matches(Regex("[A-Za-z0-9_-]{1,40}"))) {
                this@MainActivity.speakLong(text, lang, id)
            }
        }

        @JavascriptInterface
        fun stopSpeaking() = runOnUiThread { tts?.stop() }

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
            // Liessure wants a sign-in: do it in a browser tab, not here (passkeys don't work in a WebView).
            if (uri.host == home.host && uri.path == "/auth/login" && uri.getQueryParameter("app") == null) {
                startSignIn()
                return true
            }
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
            showHelp(view, "Couldn't reach Liessure.", error.description.toString())
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
            if (!request.isForMainFrame || errorResponse.statusCode != 403) return
            val reason = errorResponse.responseHeaders?.entries
                ?.firstOrNull { it.key.equals("x-gatekeeper-reason", ignoreCase = true) }?.value.orEmpty()
            if ("client-cert" in reason) {
                // Off the home network the gatekeeper wants this phone's client certificate, and got none.
                showHelp(view, "This phone didn't show a certificate.",
                    "Away from home, Liessure needs your client certificate in Android's credential store " +
                        "(Firefox keeps its own, which other apps can't use). Install the .p12 file once, then try again.")
            }
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

    /** A small page of our own, with ways out: install or re-pick the certificate, or try again. */
    private fun showHelp(view: WebView, title: String, detail: String) {
        val esc = { s: String -> s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") }
        val btn = "font-size:16px;padding:10px 18px;border-radius:8px;border:0;margin:6px;"
        val html = """
            <html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="background:#14161a;color:#eceae6;font-family:sans-serif;padding:24px;
              display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:85vh;text-align:center">
            <p style="font-size:18px">${esc(title)}</p><p style="color:#9aa0a8;font-size:14px;max-width:30em">${esc(detail)}</p>
            <button style="${btn}background:#7cc4ad" onclick="LiessureAndroid.retry()">Try again</button>
            <button style="${btn}background:#2a2e35;color:#eceae6" onclick="LiessureAndroid.installCert()">Install certificate</button>
            <button style="${btn}background:#2a2e35;color:#eceae6" onclick="LiessureAndroid.chooseCertAgain()">Choose another certificate</button>
            </body></html>
        """.trimIndent()
        view.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    private fun pickCert() {
        val pick = Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*")
            .putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("application/x-pkcs12", "application/pkcs12", "application/octet-stream"))
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(pick, PICK_CERT)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "No file picker on this phone", Toast.LENGTH_SHORT).show()
        }
    }

    private fun forgetCert() {
        prefs.edit().remove("cert_alias").apply()
        // The WebView also remembers a choice per host; clear it so the picker shows again.
        WebView.clearClientCertPreferences { loadFor(intent) }
    }

    @Deprecated("Activity results; fine for two one-off requests")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            PICK_CERT -> {
                val uri = data?.data
                if (resultCode != RESULT_OK || uri == null) return
                val bytes = runCatching { contentResolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
                if (bytes == null || bytes.isEmpty() || bytes.size > 1_000_000) {
                    Toast.makeText(this, "Couldn't read that file", Toast.LENGTH_SHORT).show()
                    return
                }
                val install = KeyChain.createInstallIntent()
                    .putExtra(KeyChain.EXTRA_PKCS12, bytes)
                    .putExtra(KeyChain.EXTRA_NAME, "Liessure")
                @Suppress("DEPRECATION")
                startActivityForResult(install, INSTALL_CERT)
            }
            INSTALL_CERT -> if (resultCode == RESULT_OK) forgetCert()
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
        const val CLAUDE = "com.anthropic.claude"
        const val PICK_CERT = 41
        const val INSTALL_CERT = 42
        val OFFLINE_ERRORS = setOf(
            WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_TIMEOUT,
        )
    }
}
