"""Build two isolated, unsigned Android 36 QA APKs. No signing or device access."""
from pathlib import Path
import hashlib
import os
import subprocess
import zipfile
import zlib

ROOT = Path(__file__).resolve().parent
SDK = Path(r"C:\Users\1thea\AppData\Local\Android\Sdk")
JDK = Path(r"C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot")
BT = SDK / "build-tools" / "36.0.0"
ANDROID = SDK / "platforms" / "android-36" / "android.jar"
ENV = os.environ.copy()
ENV["JAVA_HOME"] = str(JDK)
ENV["PATH"] = str(JDK / "bin") + os.pathsep + ENV.get("PATH", "")


def run(*args):
    print("RUN", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, env=ENV, check=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


receipts = []
for app in ("recorder", "tone"):
    base = ROOT / app
    out = ROOT / "out" / app
    classes = out / "classes"
    dex = out / "dex"
    classes.mkdir(parents=True, exist_ok=True)
    dex.mkdir(parents=True, exist_ok=True)
    sources = sorted((base / "src").rglob("*.java"))
    run(JDK / "bin" / "javac.exe", "-J-Xmx128m", "-source", "8", "-target", "8",
        "-Xlint:-options", "-classpath", ANDROID, "-d", classes, *sources)
    run(BT / "d8.bat", "-JXmx128m", "--min-api", "29", "--lib", ANDROID,
        "--output", dex, *sorted(classes.rglob("*.class")))
    raw = out / "raw.apk"
    run(BT / "aapt2.exe", "link", "-o", raw, "-I", ANDROID,
        "--manifest", base / "AndroidManifest.xml", "--min-sdk-version", "29",
        "--target-sdk-version", "36")
    with zipfile.ZipFile(raw, "a", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(dex / "classes.dex", "classes.dex")
    final = ROOT / "out" / (app + "-unsigned.apk")
    run(BT / "zipalign.exe", "-f", "-p", "4", raw, final)
    run(BT / "zipalign.exe", "-c", "-p", "4", final)
    with zipfile.ZipFile(final) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("CRC failure: " + bad)
        if "classes.dex" not in z.namelist():
            raise RuntimeError("missing classes.dex")
        crc = z.getinfo("classes.dex").CRC
    receipts += [f"{app}-unsigned.apk sha256={digest(final)} classes.dex.crc32={crc:08x}"]
    for path in [base / "AndroidManifest.xml", *sources]:
        receipts.append(f"{path.relative_to(ROOT)} sha256={digest(path)}")
for path in (ROOT / "build.py", ROOT / "README.md"):
    receipts.append(f"{path.relative_to(ROOT)} sha256={digest(path)}")
(ROOT / "out" / "receipt.txt").write_text("\n".join(receipts) + "\n", encoding="utf-8")
print("\n".join(receipts))


