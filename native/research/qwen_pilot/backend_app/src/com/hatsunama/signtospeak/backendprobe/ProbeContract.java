// SPDX-License-Identifier: Apache-2.0
package com.hatsunama.signtospeak.backendprobe;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;

final class ProbeContract {
    private static final Pattern RUN_ID = Pattern.compile("[0-9a-f]{32}");

    private ProbeContract() {}

    static Request parse(String runId, String backend, String testCase) {
        if (runId == null || !RUN_ID.matcher(runId).matches()) {
            throw new IllegalArgumentException("runid must contain exactly 32 lowercase hex characters");
        }
        if (!"CPU".equals(backend) && !"Vulkan0".equals(backend)) {
            throw new IllegalArgumentException("backend must be CPU or Vulkan0");
        }
        if (!"q4_n1".equals(testCase) && !"q4_n2".equals(testCase) && !"q6".equals(testCase)) {
            throw new IllegalArgumentException("case must be q4_n1, q4_n2, or q6");
        }
        return new Request(runId, backend, testCase);
    }

    static final class Request {
        final String runId;
        final String backend;
        final String testCase;

        private Request(String runId, String backend, String testCase) {
            this.runId = runId;
            this.backend = backend;
            this.testCase = testCase;
        }

        Path createRunDirectory(Path filesDir) throws IOException {
            return Files.createDirectory(filesDir.resolve("qa-" + runId));
        }
    }
}
