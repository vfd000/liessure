plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val liessureUrl: String = providers.gradleProperty("liessureUrl").get()

// Release signing comes from CI secrets when present (so installs can be
// updated in place); otherwise the build falls back to the debug key.
val keystorePath: String? = System.getenv("LIESSURE_KEYSTORE_PATH")

android {
    namespace = "tv.liess.liessure"
    compileSdk = 35

    defaultConfig {
        applicationId = "tv.liess.liessure"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
        versionName = "0.1.${System.getenv("GITHUB_RUN_NUMBER") ?: "0"}"
        buildConfigField("String", "LIESSURE_URL", "\"$liessureUrl\"")
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("LIESSURE_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("LIESSURE_KEY_ALIAS")
                keyPassword = System.getenv("LIESSURE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystorePath != null) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // FileProvider: hand a photo to the Claude app (Ask Claude) without exposing a file path.
    implementation("androidx.core:core:1.13.1")
    // Passkeys in the WebView (Zitadel's sign-in at sso.liess.tv): WebAuthn bridged to Credential Manager.
    // sso.liess.tv has to vouch for this app in /.well-known/assetlinks.json.
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("androidx.credentials:credentials:1.6.0-beta02")
    implementation("androidx.credentials:credentials-play-services-auth:1.6.0-beta02")
}
