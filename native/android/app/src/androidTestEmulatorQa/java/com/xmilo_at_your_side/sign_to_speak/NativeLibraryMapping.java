package com.xmilo_at_your_side.sign_to_speak;

import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;

/** Test-only evidence reader. Never loads a library or changes the application. */
public final class NativeLibraryMapping {
    private NativeLibraryMapping() {}

    public static boolean executableMapping(String library, String maps, String apk, String nativeDirectory)
            throws IOException {
        if (!library.matches("lib[a-zA-Z0-9_]+\\.so")) throw new IllegalArgumentException("Library name");
        for (String line : maps.split("\\n")) {
            String[] fields = line.trim().split("\\s+", 6);
            if (fields.length == 6 && fields[1].contains("x")
                    && fields[5].equals(nativeDirectory + "/" + library)) return true;
        }
        long[] range = storedEntry(apk, "lib/x86_64/" + library);
        if (range == null) return false;
        for (String line : maps.split("\\n")) {
            String[] fields = line.trim().split("\\s+", 6);
            if (fields.length != 6 || !fields[1].contains("x") || !fields[5].equals(apk)) continue;
            long offset = Long.parseLong(fields[2], 16);
            if (offset >= range[0] && offset < range[1]) return true;
        }
        return false;
    }

    static long[] storedEntry(String apk, String name) throws IOException {
        try (RandomAccessFile file = new RandomAccessFile(apk, "r")) {
            long length = file.length(), eocd = -1;
            for (long pos = length - 22; pos >= Math.max(0, length - 65557); pos--) {
                file.seek(pos);
                if (u32(file) == 0x06054b50L) {
                    file.seek(pos + 20);
                    if (pos + 22 + u16(file) == length) { eocd = pos; break; }
                }
            }
            if (eocd < 0) throw new IOException("ZIP end record missing");
            file.seek(eocd + 10); int entries = u16(file);
            file.seek(eocd + 16); long cursor = u32(file);
            if (entries == 0xffff || cursor == 0xffffffffL) throw new IOException("ZIP64 unsupported in test reader");
            for (int i = 0; i < entries; i++) {
                file.seek(cursor);
                if (u32(file) != 0x02014b50L) throw new IOException("ZIP directory signature");
                file.seek(cursor + 10); int method = u16(file);
                file.seek(cursor + 20); long size = u32(file);
                file.seek(cursor + 28); int nameLength = u16(file), extraLength = u16(file), commentLength = u16(file);
                file.seek(cursor + 42); long local = u32(file);
                byte[] bytes = new byte[nameLength]; file.readFully(bytes);
                if (new String(bytes, StandardCharsets.UTF_8).equals(name)) {
                    if (method != 0 || size == 0 || local == 0xffffffffL) return null;
                    file.seek(local); if (u32(file) != 0x04034b50L) throw new IOException("ZIP local signature");
                    file.seek(local + 26); int localName = u16(file), localExtra = u16(file);
                    long start = local + 30 + localName + localExtra;
                    if (start + size > cursor) throw new IOException("ZIP entry bounds");
                    return new long[]{start, start + size};
                }
                cursor += 46L + nameLength + extraLength + commentLength;
            }
            return null;
        }
    }
    private static int u16(RandomAccessFile f) throws IOException { return f.readUnsignedByte() | f.readUnsignedByte() << 8; }
    private static long u32(RandomAccessFile f) throws IOException { return (long) u16(f) | (long) u16(f) << 16; }
}
