package com.xmilo_at_your_side.sign_to_speak;

import android.media.AudioAttributes;
import android.media.AudioManager;
import org.robolectric.annotation.Implementation;
import org.robolectric.annotation.Implements;
import org.robolectric.shadows.ShadowAudioManager;

/** Service boundary for unrelated camera/window tests; not a real mixer/capture assertion. */
@Implements(AudioManager.class)
public class CaptureAudioManagerShadow extends ShadowAudioManager {
    private int policy = AudioAttributes.ALLOW_CAPTURE_BY_ALL;
    @Implementation(minSdk = 29) protected void setAllowedCapturePolicy(int value) { policy = value; }
    @Implementation(minSdk = 29) protected int getAllowedCapturePolicy() { return policy; }
}
