import java.net.URI
import java.security.MessageDigest
import groovy.json.JsonSlurper

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }

// The embedded Tailscale library is locally built native output and is never
// committed. Its root folder holds arm64-v8a/libassistant_tailnet.so and
// arm64-v8a/provenance.json (written by tools/build-tailnet.ps1).
val tailnetDirSetting=(findProperty("tailnet.nativeDir") as String?)?.takeIf{it.isNotBlank()}
    ?: System.getenv("PA_TAILNET_NATIVE_DIR")?.takeIf{it.isNotBlank()}
val tailnetNativeDir:File=tailnetDirSetting?.let{
    val dir=File(it)
    if(!dir.isAbsolute)throw GradleException("tailnet.nativeDir / PA_TAILNET_NATIVE_DIR must be an absolute path: $it")
    dir
} ?: file("src/main/jniLibs")

android {
    namespace = "org.personalassistant.app"
    compileSdk = 35
    defaultConfig {
        applicationId = "org.personalassistant.app"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-dev"
    }
    sourceSets { getByName("main") { jniLibs.setSrcDirs(listOf(tailnetNativeDir)) } }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    testOptions {
        unitTests.isIncludeAndroidResources = true
        unitTests.all {
            it.maxHeapSize = "512m"
            it.systemProperty("robolectric.dependency.repo.url", "https://repo.maven.apache.org/maven2")
            val proxy=System.getenv("HTTPS_PROXY")
            if(!proxy.isNullOrBlank()){
                val uri=URI(proxy)
                if(uri.userInfo==null){
                    it.systemProperty("https.proxyHost",uri.host)
                    it.systemProperty("https.proxyPort",uri.port.toString())
                }
            }
            it.systemProperty("sun.net.client.defaultConnectTimeout","15000")
            it.systemProperty("sun.net.client.defaultReadTimeout","30000")
            it.systemProperty("robolectric.offline", "true")
            it.systemProperty("robolectric.dependency.dir", System.getenv("PA_ROBOLECTRIC_DIR") ?: rootProject.file("../.tools/robolectric").absolutePath)
        }
    }
}
dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.14.1")
}

val verifyTailnet by tasks.registering {
    val library=File(tailnetNativeDir,"arm64-v8a/libassistant_tailnet.so")
    val manifest=File(tailnetNativeDir,"arm64-v8a/provenance.json")
    val nativeSources=rootProject.fileTree("tailnet") { include("**/*.go","**/*.c","go.mod","go.sum") }
    val builder=rootProject.file("../tools/build-tailnet.ps1")
    val configured=tailnetDirSetting!=null
    // inputs.files, unlike inputs.file, lets a missing library reach the explanation below.
    inputs.files(library,manifest);inputs.files(nativeSources);inputs.file(builder)
    doLast {
        val missing=listOf(library,manifest).filterNot{it.isFile}
        if(missing.isNotEmpty())throw GradleException(buildString{
            appendLine("Embedded Tailscale library is missing (it is built locally and not committed):")
            missing.forEach{appendLine("  "+it.absolutePath)}
            appendLine(if(configured)"Native folder set by -Ptailnet.nativeDir or PA_TAILNET_NATIVE_DIR: $tailnetNativeDir"
                       else "Default native folder: $tailnetNativeDir")
            appendLine("Build it with tools/build-tailnet.ps1 [-OutputDir <absolute folder>], or point Gradle at an")
            appendLine("existing build with -Ptailnet.nativeDir=<absolute folder> or PA_TAILNET_NATIVE_DIR.")
            append("See docs/android-tailnet.md.")
        })
        fun sha(file:java.io.File)=MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString(""){"%02X".format(it)}
        val record=JsonSlurper().parse(manifest) as Map<*,*>
        val actual=(nativeSources.files+builder).associate{it.relativeTo(rootProject.projectDir.parentFile).invariantSeparatorsPath to sha(it)}
        check(record["sources"]==actual&&record["sha256"]==sha(library)){"Embedded connection binary at $tailnetNativeDir is stale or changed; rebuild with tools/build-tailnet.ps1"}
    }
}
tasks.configureEach {
    if(name.startsWith("merge")&&name.endsWith("NativeLibs"))dependsOn(verifyTailnet)
}
