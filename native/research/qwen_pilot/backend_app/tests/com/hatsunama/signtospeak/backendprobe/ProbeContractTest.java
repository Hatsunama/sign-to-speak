// SPDX-License-Identifier: Apache-2.0
package com.hatsunama.signtospeak.backendprobe;

import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;

public final class ProbeContractTest {
    private static final String RUN_ID = "0123456789abcdef0123456789abcdef";

    public static void main(String[] args) throws Exception {
        ProbeContract.Request request = ProbeContract.parse(RUN_ID, "CPU", "q4_n1");
        check(RUN_ID.equals(request.runId), "Validated run ID");
        check("CPU".equals(request.backend), "Validated CPU backend");
        check("q4_n1".equals(request.testCase), "Validated q4_n1 case");
        ProbeContract.parse(RUN_ID, "Vulkan0", "q4_n2");
        ProbeContract.parse(RUN_ID, "Vulkan0", "q6");
        ProbeContract.parse("ffffffffffffffffffffffffffffffff", "CPU", "q6");

        for (String invalidRunId : new String[]{
                null, "", "0123456789abcdef0123456789abcde", RUN_ID + "0",
                "0123456789ABCDEF0123456789ABCDEF",
                "../" + RUN_ID.substring(3), "/" + RUN_ID.substring(1),
                RUN_ID.substring(1) + "\\", RUN_ID.substring(1) + "\n"
        }) {
            rejects(invalidRunId, "CPU", "q4_n1");
        }
        for (String invalidBackend : new String[]{null, "", "cpu", "Vulkan1", "../CPU", "CPU -j 8"}) {
            rejects(RUN_ID, invalidBackend, "q4_n1");
        }
        for (String invalidCase : new String[]{null, "", "Q6", "q4_n3", "../q6", "q6\n", ".*"}) {
            rejects(RUN_ID, "Vulkan0", invalidCase);
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

    private static void rejects(String runId, String backend, String testCase) {
        try {
            ProbeContract.parse(runId, backend, testCase);
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
