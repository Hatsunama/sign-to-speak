// SPDX-License-Identifier: Apache-2.0
#include <jni.h>

#include <android/log.h>
#include <fcntl.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cstdio>
#include <exception>
#include <string>
#include <string_view>

int sign_backend_ops_main(int argc, char ** argv);

namespace {

constexpr int probe_failure = 70;

class Fd {
public:
    explicit Fd(int fd) : fd_(fd) {}
    ~Fd() { if (fd_ >= 0) close(fd_); }

    Fd(const Fd &) = delete;
    Fd & operator=(const Fd &) = delete;

    int get() const { return fd_; }

private:
    int fd_;
};

class UtfChars {
public:
    UtfChars(JNIEnv * env, jstring input)
        : env_(env), input_(input), chars_(input ? env->GetStringUTFChars(input, nullptr) : nullptr) {}
    ~UtfChars() { if (chars_) env_->ReleaseStringUTFChars(input_, chars_); }

    UtfChars(const UtfChars &) = delete;
    UtfChars & operator=(const UtfChars &) = delete;

    const char * get() const { return chars_; }

private:
    JNIEnv * env_;
    jstring input_;
    const char * chars_;
};

class Capture {
public:
    explicit Capture(int log) : log_(log) {}
    ~Capture() { restore(); }

    Capture(const Capture &) = delete;
    Capture & operator=(const Capture &) = delete;

    bool redirect() {
        if (saved_stdout_.get() < 0 || saved_stderr_.get() < 0 ||
                std::fflush(stdout) != 0 || std::fflush(stderr) != 0) {
            return false;
        }
        stdout_redirected_ = dup2(log_, STDOUT_FILENO) >= 0;
        if (!stdout_redirected_) {
            return false;
        }
        stderr_redirected_ = dup2(log_, STDERR_FILENO) >= 0;
        return stderr_redirected_;
    }

    bool restore() {
        bool ok = true;
        if (stdout_redirected_) {
            const bool flushed = std::fflush(stdout) == 0;
            ok = dup2(saved_stdout_.get(), STDOUT_FILENO) >= 0 && flushed;
            stdout_redirected_ = false;
        }
        if (stderr_redirected_) {
            const bool flushed = std::fflush(stderr) == 0;
            ok = dup2(saved_stderr_.get(), STDERR_FILENO) >= 0 && flushed && ok;
            stderr_redirected_ = false;
        }
        return ok;
    }

private:
    int log_;
    Fd saved_stdout_{fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3)};
    Fd saved_stderr_{fcntl(STDERR_FILENO, F_DUPFD_CLOEXEC, 3)};
    bool stdout_redirected_ = false;
    bool stderr_redirected_ = false;
};

const char * case_filter(std::string_view test_case) {
    if (test_case == "q4_n1") {
        return "^type_a=q4_K,type_b=f32,m=4096,n=1,k=1024,";
    }
    if (test_case == "q4_n2") {
        return "^type_a=q4_K,type_b=f32,m=4096,n=2,k=1024,";
    }
    if (test_case == "q6") {
        return "^type_a=q6_K,type_b=f32,m=1000,n=21,k=4608,";
    }
    return nullptr;
}

int run_probe(int log, const char * backend, const char * filter) {
    Capture capture(log);
    if (!capture.redirect()) {
        dprintf(log, "probe: stdout/stderr redirection failed (errno %d)\n", errno);
        return probe_failure;
    }

    int result = probe_failure;
    try {
        std::array<std::string, 10> arguments{
            "test-backend-ops", "test", "-j", "1", "-b", backend, "-o", "MUL_MAT", "-p", filter,
        };
        std::array<char *, arguments.size() + 1> argv{};
        for (size_t i = 0; i < arguments.size(); ++i) {
            argv[i] = arguments[i].data();
        }
        dprintf(log, "probe: entering test-backend-ops backend=%s filter=%s\n", backend, filter);
        result = sign_backend_ops_main(static_cast<int>(arguments.size()), argv.data());
    } catch (const std::exception & error) {
        dprintf(log, "probe: native exception: %s\n", error.what());
    } catch (...) {
        dprintf(log, "probe: unknown native exception\n");
    }

    if (!capture.restore()) {
        dprintf(log, "probe: stdout/stderr flush or restore failed\n");
        return probe_failure;
    }
    return result;
}

} // namespace

extern "C" JNIEXPORT jint JNICALL
Java_com_hatsunama_signtospeak_backendprobe_ProbeInstrumentation_runNative(
        JNIEnv * env, jclass, jstring java_dir, jstring java_backend, jstring java_case) {
    UtfChars dir(env, java_dir);
    if (!dir.get()) {
        return probe_failure;
    }
    UtfChars backend(env, java_backend);
    if (!backend.get()) {
        return probe_failure;
    }
    UtfChars test_case(env, java_case);
    if (!test_case.get()) {
        return probe_failure;
    }
    const char * filter = case_filter(test_case.get());
    std::string_view backend_name = backend.get();
    if (!filter || (backend_name != "CPU" && backend_name != "Vulkan0")) {
        return probe_failure;
    }

    const Fd directory(open(dir.get(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    if (directory.get() < 0) {
        __android_log_print(ANDROID_LOG_ERROR, "SignBackendProbe",
                            "Opening the run directory failed (errno %d)", errno);
        return probe_failure;
    }
    const Fd log(openat(directory.get(), "stdout.log",
                       O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600));
    if (log.get() < 0) {
        __android_log_print(ANDROID_LOG_ERROR, "SignBackendProbe",
                            "Creating stdout.log failed (errno %d)", errno);
        return probe_failure;
    }
    return run_probe(log.get(), backend.get(), filter);
}
