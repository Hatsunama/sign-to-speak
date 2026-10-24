package com.xmilo_at_your_side.sign_to_speak;

import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry;
import androidx.test.runner.lifecycle.Stage;
import androidx.test.uiautomator.Configurator;
import java.util.Collection;
import java.util.concurrent.atomic.AtomicReference;

/** Bounded lifecycle control for the real QA Activity. */
final class PhoneActivityDriver implements AutoCloseable {
    private static final long WAIT_MS = 45000;
    private final Instrumentation instrumentation;
    private final Context target;
    private final Configurator configurator = Configurator.getInstance();
    private final long originalIdleTimeout;
    private MainActivity activity;
    private boolean closed;

    PhoneActivityDriver(Instrumentation instrumentation) {
        this.instrumentation = instrumentation;
        this.target = instrumentation.getTargetContext();
        originalIdleTimeout = configurator.getWaitForIdleTimeout();
        // The canonical renderer animates continuously, so global UI idle is not a valid readiness signal.
        configurator.setWaitForIdleTimeout(0);
    }

    MainActivity launch() throws Exception {
        if (closed || activity != null) throw new IllegalStateException("Driver already used");
        Intent intent = new Intent(target, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        target.startActivity(intent);
        long until = SystemClock.elapsedRealtime() + WAIT_MS;
        while (SystemClock.elapsedRealtime() < until) {
            MainActivity resumed = onMain(() -> {
                Collection<android.app.Activity> activities = ActivityLifecycleMonitorRegistry.getInstance()
                        .getActivitiesInStage(Stage.RESUMED);
                for (android.app.Activity candidate : activities) {
                    if (candidate instanceof MainActivity && !candidate.isFinishing()
                            && !candidate.isDestroyed()) return (MainActivity) candidate;
                }
                return null;
            });
            if (resumed != null) {
                activity = resumed;
                return resumed;
            }
            SystemClock.sleep(25);
        }
        throw new AssertionError("Timed out waiting for real MainActivity RESUMED");
    }

    @Override public void close() throws Exception {
        if (closed) return;
        closed = true;
        try {
            MainActivity launched = activity;
            if (launched != null) {
                onMain(() -> {
                    if (!launched.isDestroyed()) launched.finish();
                    return null;
                });
                long until = SystemClock.elapsedRealtime() + WAIT_MS;
                while (SystemClock.elapsedRealtime() < until) {
                    if (onMain(launched::isDestroyed)) return;
                    SystemClock.sleep(25);
                }
                throw new AssertionError("Timed out waiting for real MainActivity destruction");
            }
        } finally {
            configurator.setWaitForIdleTimeout(originalIdleTimeout);
        }
    }

    private <T> T onMain(java.util.concurrent.Callable<T> work) throws Exception {
        AtomicReference<T> result = new AtomicReference<>();
        AtomicReference<Throwable> error = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { result.set(work.call()); } catch (Throwable failure) { error.set(failure); }
        });
        if (error.get() instanceof Error) throw (Error) error.get();
        if (error.get() instanceof Exception) throw (Exception) error.get();
        return result.get();
    }
}
