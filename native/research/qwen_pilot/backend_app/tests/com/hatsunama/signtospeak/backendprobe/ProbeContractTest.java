// SPDX-License-Identifier: Apache-2.0
package com.hatsunama.signtospeak.backendprobe;

import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

public final class ProbeContractTest {
    private static final String RUN_ID = "0123456789abcdef0123456789abcdef";

    public static void main(String[] args) throws Exception {
        ProbeContract.Request request = ProbeContract.parse(RUN_ID, "CPU", "q4_n1", "baseline");
        check(RUN_ID.equals(request.runId), "Validated run ID");
        check("CPU".equals(request.backend), "Validated CPU backend");
        check("q4_n1".equals(request.testCase), "Validated q4_n1 case");
        check("baseline".equals(request.vkMode), "CPU baseline mode");
        check(request.vkEnvironment.isEmpty(), "CPU baseline environment");
        ProbeContract.parse("ffffffffffffffffffffffffffffffff", "CPU", "q6", "baseline");

        Map<String, String> f32 = Collections.singletonMap("GGML_VK_DISABLE_F16", "1");
        Map<String, String> mmvq = Collections.singletonMap("GGML_VK_FORCE_MMVQ", "1");
        Map<String, String> both = new HashMap<>(f32);
        both.putAll(mmvq);
        checkMode("q4_n2", "baseline", Collections.emptyMap());
        checkMode("q4_n1", "f32", f32);
        checkMode("q4_n2", "mmvq", mmvq);
        checkMode("q6", "f32_mmvq", both);

        for (String invalidMode : new String[]{null, "", "F32", "baseline ", "mmvq\n", "f32;echo"}) {
            rejects(RUN_ID, "Vulkan0", "q4_n1", invalidMode);
        }
        for (String mode : new String[]{"f32", "mmvq", "f32_mmvq"}) {
            rejects(RUN_ID, "CPU", "q4_n1", mode);
        }

        for (String invalidRunId : new String[]{
                null, "", "0123456789abcdef0123456789abcde", RUN_ID + "0",
                "0123456789ABCDEF0123456789ABCDEF",
                "../" + RUN_ID.substring(3), "/" + RUN_ID.substring(1),
                RUN_ID.substring(1) + "\\", RUN_ID.substring(1) + "\n"
        }) {
            rejects(invalidRunId, "CPU", "q4_n1", "baseline");
        }
        for (String invalidBackend : new String[]{null, "", "cpu", "Vulkan1", "../CPU", "CPU -j 8"}) {
            rejects(RUN_ID, invalidBackend, "q4_n1", "baseline");
        }
        for (String invalidCase : new String[]{null, "", "Q6", "q4_n3", "../q6", "q6\n", ".*"}) {
            rejects(RUN_ID, "Vulkan0", invalidCase, "baseline");
        }

        Path filesDir = Files.createTempDirectory("sign-backend-probe-test-");
        Path runDir = filesDir.resolve("qa-" + RUN_ID);
        Path marker = runDir.resolve("marker");
        try {
            check(runDir.equals(request.createRunDirectory(filesDir)), "Fresh run directory path");
            check(Files.isDirectory(runDir), "Fresh run directory exists");
            Files.createFile(marker);
            try {
                request.createRunDirectory(filesDir);
                throw new AssertionError("Existing run directory accepted");
            } catch (FileAlreadyExistsException expected) {
                check(Files.isRegularFile(marker), "Duplicate request preserved the prior run");
            }

            Files.delete(marker);
            Files.delete(runDir);
            Files.createFile(runDir);
            try {
                request.createRunDirectory(filesDir);
                throw new AssertionError("Existing run path accepted");
            } catch (FileAlreadyExistsException expected) {
                check(Files.isRegularFile(runDir), "Conflicting file preserved");
            }
        } finally {
            if (Files.isDirectory(runDir)) {
                Files.deleteIfExists(marker);
            }
            Files.deleteIfExists(runDir);
            Files.delete(filesDir);
        }

        System.out.println("ProbeContractTest passed");
    }

    private static void checkMode(String testCase, String vkMode, Map<String, String> expected) {
        ProbeContract.Request request = ProbeContract.parse(RUN_ID, "Vulkan0", testCase, vkMode);
        check(vkMode.equals(request.vkMode), "Validated Vulkan mode " + vkMode);
        check(expected.equals(request.vkEnvironment), "Exact environment for " + vkMode);
        try {
            request.vkEnvironment.put("GGML_VK_DISABLE_F16", "0");
            throw new AssertionError("Mutable Vulkan environment for " + vkMode);
        } catch (UnsupportedOperationException expectedFailure) {
            check(expected.equals(request.vkEnvironment), "Unchanged environment for " + vkMode);
        }
    }

    private static void rejects(String runId, String backend, String testCase, String vkMode) {
        try {
            ProbeContract.parse(runId, backend, testCase, vkMode);
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("Invalid request accepted");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
