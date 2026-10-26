fn main() {
    let target_os = std::env::var("CARGO_CFG_TARGET_OS")
        .expect("Cargo must provide CARGO_CFG_TARGET_OS to the build script");

    if matches!(target_os.as_str(), "android" | "linux") {
        // The JNI consumer must record a portable basename in DT_NEEDED.
        println!("cargo:rustc-link-arg-cdylib=-Wl,-soname,libsign_pocket_tts_runtime.so");
    }

    if target_os == "android" {
        println!("cargo:rustc-link-arg-cdylib=-Wl,-z,max-page-size=16384");
    }
}
