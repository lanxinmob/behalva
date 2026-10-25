import java.net.URI
import java.security.MessageDigest
import groovy.json.JsonSlurper

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
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
    val library=layout.projectDirectory.file("src/main/jniLibs/arm64-v8a/libassistant_tailnet.so")
    val manifest=layout.projectDirectory.file("src/main/jniLibs/arm64-v8a/provenance.json")
    val nativeSources=rootProject.fileTree("tailnet") { include("**/*.go","**/*.c","go.mod","go.sum") }
    val builder=rootProject.file("../tools/build-tailnet.ps1")
    inputs.file(library)
    inputs.file(manifest);inputs.files(nativeSources);inputs.file(builder)
    doLast {
        check(library.asFile.isFile&&manifest.asFile.isFile) { "Build the embedded connection first: tools/build-tailnet.ps1 (see docs/android-tailnet.md)" }
        fun sha(file:java.io.File)=MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString(""){"%02X".format(it)}
        val record=JsonSlurper().parse(manifest.asFile) as Map<*,*>
        val actual=(nativeSources.files+builder).associate{it.relativeTo(rootProject.projectDir.parentFile).invariantSeparatorsPath to sha(it)}
        check(record["sources"]==actual&&record["sha256"]==sha(library.asFile)){"Embedded connection binary is stale or changed; rebuild with tools/build-tailnet.ps1"}
    }
}
tasks.configureEach {
    if(name.startsWith("merge")&&name.endsWith("NativeLibs"))dependsOn(verifyTailnet)
}
