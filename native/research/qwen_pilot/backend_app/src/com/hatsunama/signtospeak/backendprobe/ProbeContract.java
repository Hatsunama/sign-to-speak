// SPDX-License-Identifier: Apache-2.0
package com.hatsunama.signtospeak.backendprobe;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Pattern;

final class ProbeContract {
    static final String VK_DISABLE_F16 = "GGML_VK_DISABLE_F16";
    static final String VK_FORCE_MMVQ = "GGML_VK_FORCE_MMVQ";
    private static final Pattern RUN_ID = Pattern.compile("[0-9a-f]{32}");

    private ProbeContract() {}

    static Request parse(String runId, String backend, String testCase, String vkMode) {
        if (runId == null || !RUN_ID.matcher(runId).matches()) {
            throw new IllegalArgumentException("runid must contain exactly 32 lowercase hex characters");
        }
        if (!"CPU".equals(backend) && !"Vulkan0".equals(backend)) {
            throw new IllegalArgumentException("backend must be CPU or Vulkan0");
        }
        if (!"q4_n1".equals(testCase) && !"q4_n2".equals(testCase) && !"q6".equals(testCase)) {
            throw new IllegalArgumentException("case must be q4_n1, q4_n2, or q6");
        }
        boolean baseline = "baseline".equals(vkMode);
        if (!baseline && !"f32".equals(vkMode) && !"mmvq".equals(vkMode) && !"f32_mmvq".equals(vkMode)) {
            throw new IllegalArgumentException("vkmode must be baseline, f32, mmvq, or f32_mmvq");
        }
        if (!baseline && !"Vulkan0".equals(backend)) {
            throw new IllegalArgumentException("Non-baseline vkmode requires Vulkan0");
        }
        return new Request(runId, backend, testCase, vkMode);
    }

    static final class Request {
        final String runId;
        final String backend;
        final String testCase;
        final String vkMode;
        final Map<String, String> vkEnvironment;

        private Request(String runId, String backend, String testCase, String vkMode) {
            this.runId = runId;
            this.backend = backend;
            this.testCase = testCase;
            this.vkMode = vkMode;
            Map<String, String> environment = new LinkedHashMap<>();
            if ("f32".equals(vkMode) || "f32_mmvq".equals(vkMode)) {
                environment.put(VK_DISABLE_F16, "1");
            }
            if ("mmvq".equals(vkMode) || "f32_mmvq".equals(vkMode)) {
                environment.put(VK_FORCE_MMVQ, "1");
            }
            this.vkEnvironment = Collections.unmodifiableMap(environment);
        }

        Path createRunDirectory(Path filesDir) throws IOException {
            return Files.createDirectory(filesDir.resolve("qa-" + runId));
        }
    }
}
