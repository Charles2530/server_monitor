package com.charles.monitor;

import org.junit.Test;

/** ZXing 4.3.0's published POM omits its AndroidX implementation dependencies. */
public class ScannerRuntimeTest {
    @Test public void scannerRuntimeIncludesPermissionAndActivityDependencies() throws Exception {
        ClassLoader loader = getClass().getClassLoader();
        for (String name : new String[] {
                "androidx.core.content.ContextCompat",
                "androidx.core.app.ActivityCompat",
                "androidx.fragment.app.Fragment",
                "androidx.activity.result.contract.ActivityResultContract",
                "com.journeyapps.barcodescanner.CaptureActivity"
        }) {
            Class.forName(name, false, loader);
        }
    }
}
