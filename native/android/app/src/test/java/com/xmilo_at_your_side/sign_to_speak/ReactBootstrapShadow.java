package com.xmilo_at_your_side.sign_to_speak;

import android.content.Context;
import com.facebook.react.ReactNativeApplicationEntryPoint;
import org.robolectric.annotation.Implementation;
import org.robolectric.annotation.Implements;

/** Only replaces native-library loading; production Application policy still executes. */
@Implements(value = ReactNativeApplicationEntryPoint.class, isInAndroidSdk = false)
public class ReactBootstrapShadow {
    @Implementation public static void loadReactNative(Context context) {}
}
