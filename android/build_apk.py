"""Build a standalone Android APK with the Android command-line SDK tools.

No Gradle / Android Studio required.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"
SDK = Path(os.getenv("ANDROID_HOME", r"C:\Android\sdk"))
BUILD_TOOLS = SDK / "build-tools" / "35.0.0"
PLATFORM = SDK / "platforms" / "android-34" / "android.jar"
JAVA_HOME = Path(os.getenv("JAVA_HOME", r"C:\Program Files\Microsoft\jdk-21.0.12.101-hotspot"))
JAVA_BIN = JAVA_HOME / "bin"
WS_JARS = sorted((ANDROID / "libs").glob("*.jar"))
SOURCE_DIR = ANDROID / "src" / "com" / "rti" / "interpreter"
SOURCES = sorted(SOURCE_DIR.glob("*.java"))
MANIFEST = ANDROID / "AndroidManifest.xml"
BUILD = ANDROID / "build"
DEX = BUILD / "dex"
CLASSES = BUILD / "classes"
CLASS_JAR = BUILD / "classes.jar"
BASE_APK = BUILD / "base.apk"
ALIGNED_APK = BUILD / "aligned.apk"
OUT = ROOT / "dist" / "RTI-android.apk"
KEYSTORE = ANDROID / "debug.keystore"


def require(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"missing {label}: {path}")


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(str(part) for part in command))
    env = os.environ.copy()
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = str(JAVA_BIN) + os.pathsep + env.get("PATH", "")
    subprocess.run(command, cwd=cwd, check=True, env=env)


def main() -> int:
    require(PLATFORM, "android.jar")
    require(BUILD_TOOLS / "aapt2.exe", "aapt2")
    require(BUILD_TOOLS / "d8.bat", "d8")
    require(BUILD_TOOLS / "zipalign.exe", "zipalign")
    require(BUILD_TOOLS / "apksigner.bat", "apksigner")
    if not WS_JARS:
        raise SystemExit(f"missing Java-WebSocket / slf4j jars in {ANDROID / 'libs'}")
    require(SOURCE_DIR, "Java sources")
    if not SOURCES:
        raise SystemExit(f"no Java sources in {SOURCE_DIR}")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    DEX.mkdir(parents=True)
    CLASSES.mkdir(parents=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    run([
        str(BUILD_TOOLS / "aapt2.exe"),
        "link",
        "-o",
        str(BASE_APK),
        "-I",
        str(PLATFORM),
        "--manifest",
        str(MANIFEST),
        "--min-sdk-version",
        "26",
        "--target-sdk-version",
        "34",
        "--version-code",
        "4",
        "--version-name",
        "1.3",
    ])

    lib_classpath = os.pathsep.join(str(jar) for jar in WS_JARS)

    run([
        str(JAVA_BIN / "javac.exe"),
        "-source",
        "8",
        "-target",
        "8",
        "-Xlint:-options",
        "-bootclasspath",
        str(PLATFORM),
        "-cp",
        f"{PLATFORM}{os.pathsep}{lib_classpath}",
        "-d",
        str(CLASSES),
        *[str(source) for source in SOURCES],
    ])

    run([
        str(JAVA_BIN / "jar.exe"),
        "cf",
        str(CLASS_JAR),
        "-C",
        str(CLASSES),
        ".",
    ])

    run([
        str(JAVA_BIN / "java.exe"),
        "-cp",
        str(BUILD_TOOLS / "lib" / "d8.jar"),
        "com.android.tools.r8.D8",
        "--lib",
        str(PLATFORM),
        "--min-api",
        "26",
        "--output",
        str(DEX),
        str(CLASS_JAR),
        *[str(jar) for jar in WS_JARS],
    ])

    with zipfile.ZipFile(BASE_APK, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(DEX / "classes.dex", "classes.dex")

    run([str(BUILD_TOOLS / "zipalign.exe"), "-f", "4", str(BASE_APK), str(ALIGNED_APK)])

    if not KEYSTORE.exists():
        run([
            str(JAVA_BIN / "keytool.exe"),
            "-genkeypair",
            "-keystore",
            str(KEYSTORE),
            "-alias",
            "rti",
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-dname",
            "CN=RTI, OU=Dev, O=RTI, L=Beijing, ST=Beijing, C=CN",
            "-noprompt",
        ])

    run([
        str(JAVA_BIN / "jarsigner.exe"),
        "-keystore",
        str(KEYSTORE),
        "-storepass",
        "android",
        "-keypass",
        "android",
        str(ALIGNED_APK),
        "rti",
    ])
    run([
        str(JAVA_BIN / "java.exe"),
        "-jar",
        str(BUILD_TOOLS / "lib" / "apksigner.jar"),
        "sign",
        "--v1-signing-enabled",
        "true",
        "--v2-signing-enabled",
        "true",
        "--v3-signing-enabled",
        "true",
        "--ks",
        str(KEYSTORE),
        "--ks-pass",
        "pass:android",
        "--key-pass",
        "pass:android",
        "--out",
        str(OUT),
        str(ALIGNED_APK),
    ])
    run([
        str(JAVA_BIN / "java.exe"),
        "-jar",
        str(BUILD_TOOLS / "lib" / "apksigner.jar"),
        "verify",
        "--print-certs",
        str(OUT),
    ])
    print(f"\nbuilt: {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

