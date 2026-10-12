package com.xmilo_at_your_side.sign_to_speak;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/** Checks the forked worker itself, independently of Robolectric's SDK initialization. */
public class UnitTestRuntimeTest {
    @Test public void androidUnitTestWorkerActuallyRunsJava21() {
        String specification = System.getProperty("java.specification.version");
        System.out.println("Android unit-test worker: java.specification.version=" + specification
            + ", java.runtime.version=" + System.getProperty("java.runtime.version")
            + ", java.home=" + System.getProperty("java.home"));
        assertEquals("SDK 36 Robolectric tests must execute on Java 21; compiler target stays 17",
            "21", specification);
    }
}
