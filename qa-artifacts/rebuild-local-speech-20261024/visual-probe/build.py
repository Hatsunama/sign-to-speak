"""Compile pure Java tests and one isolated unsigned API 36 visual QA APK."""
from pathlib import Path
import hashlib
import os
import subprocess
import zipfile

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

out = ROOT / "out"
test_classes = out / "test-classes"
classes = out / "classes"
dex = out / "dex"
for directory in (test_classes, classes, dex):
    directory.mkdir(parents=True, exist_ok=True)
sources = sorted((ROOT / "src").rglob("*.java"))
counter = next(p for p in sources if p.name == "PixelCounter.java")
run(JDK / "bin" / "javac.exe", "-J-Xmx128m", "-source", "8", "-target", "8",
    "-Xlint:-options", "-d", test_classes, counter, ROOT / "test" / "PixelCounterTest.java")
run(JDK / "bin" / "java.exe", "-Xmx128m", "-cp", test_classes, "PixelCounterTest")
run(JDK / "bin" / "javac.exe", "-J-Xmx128m", "-source", "8", "-target", "8",
    "-Xlint:-options", "-classpath", ANDROID, "-d", classes, *sources)
run(BT / "d8.bat", "-JXmx128m", "--min-api", "29", "--lib", ANDROID,
    "--output", dex, *sorted(classes.rglob("*.class")))
raw = out / "raw.apk"
run(BT / "aapt2.exe", "link", "-o", raw, "-I", ANDROID,
    "--manifest", ROOT / "AndroidManifest.xml", "--min-sdk-version", "29",
    "--target-sdk-version", "36")
with zipfile.ZipFile(raw, "a", compression=zipfile.ZIP_DEFLATED) as z:
    z.write(dex / "classes.dex", "classes.dex")
final = out / "visual-capture-qa-unsigned.apk"
run(BT / "zipalign.exe", "-f", "-p", "4", raw, final)
run(BT / "zipalign.exe", "-c", "-p", "4", final)
with zipfile.ZipFile(final) as z:
    if z.testzip() is not None or "classes.dex" not in z.namelist():
        raise RuntimeError("APK CRC or classes.dex failure")
    crc = z.getinfo("classes.dex").CRC
receipt = [f"visual-capture-qa-unsigned.apk sha256={digest(final)} classes.dex.crc32={crc:08x}"]
for path in [ROOT / "AndroidManifest.xml", ROOT / "README.md", ROOT / "build.py",
             *sources, ROOT / "test" / "PixelCounterTest.java"]:
    receipt.append(f"{path.relative_to(ROOT)} sha256={digest(path)}")
(out / "receipt.txt").write_text("\n".join(receipt) + "\n", encoding="utf-8")
print("\n".join(receipt))
